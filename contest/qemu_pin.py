#!/usr/bin/env python3
"""
qemu_pin.py - Dynamically pin QEMU vCPU threads to dedicated cores
              and move other processes away from those cores.

The script reserves a set of "system" cores that are always available for
non-QEMU processes. Remaining cores are dynamically allocated to vCPU threads
as needed. When there are fewer vCPUs than available cores, the extra cores
remain available to system processes.

Requires QEMU to be started with debug-threads=on, e.g.:
    qemu-system-x86_64 -name guest=myvm,debug-threads=on ...

This causes vCPU threads to be named "CPU N/KVM" for easy identification.

Usage:
    sudo python3 qemu_pin.py [--interval SECONDS] [--system-cores 0-9]
"""

import os
import argparse
import json
import sys
import time
from pathlib import Path


AFFINITY_STATE_FILE = Path('/run/qemu_pin_affinity.json')
ALL_CPUS_MARKER = "all"


class SystemState:
    """Track system state for CPU pinning management."""

    def __init__(self, system_cpus, vcpu_pool):
        self.system_cpus = system_cpus
        self.vcpu_pool = vcpu_pool
        self.all_cpus = system_cpus | vcpu_pool

        # Original affinities: tid -> set of cpus
        self.original_affinities = {}

        # Current vCPU state
        self.vcpu_tids = set()
        self.allocated_vcpu_cores = set()

        # Previous state for change detection
        self.prev_vcpu_count = 0
        self.prev_allocated = set()

        # Cache: pid -> is_qemu (True/False)
        self.pid_is_qemu = {}

        # Load persisted state
        self._load_state()

    def _load_state(self):
        """Load original affinity map from state file."""
        if not AFFINITY_STATE_FILE.exists():
            return
        try:
            data = json.loads(AFFINITY_STATE_FILE.read_text(encoding='utf-8'))
            for tid, cpus in data.items():
                if cpus == ALL_CPUS_MARKER:
                    self.original_affinities[int(tid)] = self.all_cpus
                else:
                    self.original_affinities[int(tid)] = set(cpus)
        except (json.JSONDecodeError, ValueError, KeyError):
            pass

    def _save_state(self):
        """Save original affinity map to state file."""
        data = {}
        for tid, cpus in self.original_affinities.items():
            if cpus == self.all_cpus:
                data[str(tid)] = ALL_CPUS_MARKER
            else:
                data[str(tid)] = sorted(cpus)
        AFFINITY_STATE_FILE.write_text(json.dumps(data), encoding='utf-8')

    def scan_system(self):
        """
        Scan procfs once to collect all thread info.
        Returns (vcpu_threads, all_tids) where:
          - vcpu_threads: list of (pid, tid, name) for vCPU threads
          - all_tids: set of all thread IDs in the system
        """
        vcpu_threads = []
        all_tids = set()
        seen_pids = set()

        try:
            proc_entries = os.listdir('/proc')
        except OSError:
            return vcpu_threads, all_tids

        for entry in proc_entries:
            if not entry.isdigit():
                continue

            pid = int(entry)
            seen_pids.add(pid)
            proc_path = f'/proc/{pid}'

            # Check cache first for is_qemu
            if pid in self.pid_is_qemu:
                is_qemu = self.pid_is_qemu[pid]
            else:
                is_qemu = False
                try:
                    with open(f'{proc_path}/comm', 'r', encoding='utf-8') as f:
                        comm = f.read().strip()
                    is_qemu = comm.startswith('qemu')
                except (PermissionError, FileNotFoundError, OSError):
                    pass
                self.pid_is_qemu[pid] = is_qemu

            task_path = f'{proc_path}/task'
            try:
                task_entries = os.listdir(task_path)
            except (PermissionError, FileNotFoundError, OSError):
                continue

            for task_entry in task_entries:
                if not task_entry.isdigit():
                    continue

                tid = int(task_entry)
                all_tids.add(tid)

                # Only check thread names for QEMU processes
                if is_qemu:
                    try:
                        with open(f'{task_path}/{task_entry}/comm', 'r',
                                  encoding='utf-8') as f:
                            thread_comm = f.read().strip()
                        if thread_comm.startswith('CPU ') and '/KVM' in thread_comm:
                            vcpu_threads.append((pid, tid, thread_comm))
                    except (PermissionError, FileNotFoundError, OSError):
                        pass

        # Garbage collect stale pids from cache
        stale_pids = set(self.pid_is_qemu.keys()) - seen_pids
        for pid in stale_pids:
            del self.pid_is_qemu[pid]

        return vcpu_threads, all_tids

    def update_tids(self, current_tids):
        """
        Update tracking for current system tids.
        Records original affinities for new tids, removes stale ones.
        Returns True if state changed and was saved.
        """
        changed = False

        # Record original affinities for new tids (excluding vCPU threads)
        for tid in current_tids:
            if tid in self.vcpu_tids:
                continue
            if tid not in self.original_affinities:
                affinity = self.get_affinity(tid)
                if affinity is not None:
                    self.original_affinities[tid] = affinity
                    changed = True

        # Garbage collect stale tids
        stale = set(self.original_affinities.keys()) - current_tids
        if stale:
            changed = True
            for tid in stale:
                del self.original_affinities[tid]

        if changed:
            self._save_state()

        return changed

    def set_vcpu_tids(self, vcpu_tids):
        """Update the set of vCPU thread IDs."""
        self.vcpu_tids = vcpu_tids

    def get_original_affinity(self, tid):
        """Get the original affinity for a tid."""
        return self.original_affinities.get(tid)

    def get_system_available(self):
        """Get CPUs available for system processes."""
        return self.system_cpus | (self.vcpu_pool - self.allocated_vcpu_cores)

    def set_allocated_cores(self, cores):
        """Update the set of cores allocated to vCPUs."""
        self.allocated_vcpu_cores = cores

    def check_vcpu_change(self, num_vcpus):
        """Check if vCPU count or allocation changed. Returns True if changed."""
        if num_vcpus != self.prev_vcpu_count or \
           self.allocated_vcpu_cores != self.prev_allocated:
            self.prev_vcpu_count = num_vcpus
            self.prev_allocated = self.allocated_vcpu_cores.copy()
            return True
        return False

    def restore_all_affinities(self, current_tids):
        """Restore original affinities for all tracked processes."""
        unknown = 0
        reset_count = 0
        for tid in current_tids:
            if tid not in self.original_affinities:
                unknown += 1
                continue
            original = self.original_affinities[tid]
            current = self.get_affinity(tid)
            if current is not None and current != original:
                if self.set_affinity(tid, original):
                    reset_count += 1
        return reset_count, unknown

    @staticmethod
    def get_affinity(tid):
        """Get current CPU affinity for a thread."""
        try:
            return os.sched_getaffinity(tid)
        except (PermissionError, OSError):
            return None

    @staticmethod
    def set_affinity(tid, cpus):
        """Set CPU affinity for a thread."""
        try:
            os.sched_setaffinity(tid, cpus)
            return True
        except (PermissionError, OSError):
            return False


def parse_cpu_range(cpu_range):
    """Parse CPU range string like '0-31,64-95' into a set of CPUs."""
    cpus = set()
    for part in cpu_range.split(','):
        if '-' in part:
            start, end = part.split('-')
            cpus.update(range(int(start), int(end) + 1))
        else:
            cpus.add(int(part))
    return cpus


def format_cpu_range(cpus):
    """
    Format a set of CPUs as a compressed range string.
    Only compress ranges of 3+ consecutive CPUs.
    E.g., {1,2,3} -> "1-3", {1,2,5,6,7} -> "1,2,5-7"
    """
    if not cpus:
        return ""

    sorted_cpus = sorted(cpus)
    result = []
    i = 0

    while i < len(sorted_cpus):
        start = sorted_cpus[i]
        end = start

        # Find consecutive range
        while i + 1 < len(sorted_cpus) and sorted_cpus[i + 1] == sorted_cpus[i] + 1:
            i += 1
            end = sorted_cpus[i]

        # Only compress if range has 3+ elements
        if end - start >= 2:
            result.append(f"{start}-{end}")
        else:
            for cpu in range(start, end + 1):
                result.append(str(cpu))

        i += 1

    return ",".join(result)


def pin_vcpu_threads(state, vcpu_threads, dry_run=False, log=print):
    """
    Pin vCPU threads to dedicated cores, preserving valid existing pinnings.
    Returns set of allocated cores.
    """
    used_cores = set()
    vcpu_pinning = {}

    # First pass: identify valid existing pinnings
    for _, tid, _ in vcpu_threads:
        affinity = state.get_affinity(tid)
        if affinity and len(affinity) == 1:
            core = next(iter(affinity))
            if core in state.vcpu_pool and core not in used_cores:
                vcpu_pinning[tid] = core
                used_cores.add(core)
            else:
                vcpu_pinning[tid] = None
        else:
            vcpu_pinning[tid] = None

    # Second pass: assign cores to vCPUs that need them
    available = sorted(state.vcpu_pool - used_cores)
    pool_idx = 0

    for _, tid, name in vcpu_threads:
        if vcpu_pinning[tid] is not None:
            continue

        if pool_idx < len(available):
            target = {available[pool_idx]}
            pool_idx += 1
        else:
            target = state.vcpu_pool

        current = state.get_affinity(tid)
        if current != target:
            if dry_run:
                log(f"Would pin {name} (tid={tid}) to CPU {target}")
            else:
                if state.set_affinity(tid, target):
                    log(f"Pinned {name} (tid={tid}) to CPU {target}")

    return used_cores | set(available[:pool_idx])


def adjust_system_affinities(state, current_tids, dry_run=False):
    """Adjust affinities of non-vCPU processes based on available cores."""
    moved = 0
    expanded = 0
    skipped = 0
    system_available = state.get_system_available()

    for tid in current_tids:
        if tid in state.vcpu_tids:
            continue

        original = state.get_original_affinity(tid)
        if original is None:
            skipped += 1
            continue

        current = state.get_affinity(tid)
        if current is None:
            skipped += 1
            continue

        # Calculate ideal: original AND available
        ideal = original & system_available
        if not ideal:
            ideal = system_available

        if current != ideal:
            if dry_run:
                pass
            else:
                if state.set_affinity(tid, ideal):
                    if len(ideal) > len(current):
                        expanded += 1
                    else:
                        moved += 1

    return moved, expanded, skipped


def main():
    """Main entry point for the QEMU CPU pinning daemon."""
    parser = argparse.ArgumentParser(
        description='Dynamically pin QEMU vCPU threads to dedicated cores')
    parser.add_argument('--interval', type=float, default=5.0,
                        help='How often to check and repin (seconds)')
    parser.add_argument('--system-cores', type=str, default=None,
                        help='CPU cores always reserved for system (e.g., "0-9"). '
                             'Default: first 1/4 of CPUs')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print what would be done without doing it')
    parser.add_argument('--once', action='store_true',
                        help='Run once and exit')
    parser.add_argument('--timing-threshold', type=float, default=25.0,
                        help='Print timing info if iteration takes longer than this (ms). '
                             'Set to 0 to always print. Default: 25')
    args = parser.parse_args()

    total_cpus = os.cpu_count()

    if args.system_cores:
        system_cpus = parse_cpu_range(args.system_cores)
    else:
        system_cpus = set(range(max(1, total_cpus // 4)))

    vcpu_pool = set(range(total_cpus)) - system_cpus

    if not vcpu_pool:
        print("Error: No CPUs available for vCPU pool!")
        return 1

    print(f"System cores (always for non-QEMU): {format_cpu_range(system_cpus)}")
    print(f"vCPU pool (can be dedicated to vCPUs): {format_cpu_range(vcpu_pool)}")
    print(f"Checking every {args.interval} seconds...")
    print()

    state = SystemState(system_cpus, vcpu_pool)

    while True:
        iter_start = time.monotonic()
        t_adjust = None  # Will be set if vcpu_threads exist

        # Scan system once to get all thread info
        vcpu_threads, current_tids = state.scan_system()
        t_scan = time.monotonic()

        vcpu_tids = {tid for _, tid, _ in vcpu_threads}

        state.set_vcpu_tids(vcpu_tids)

        # Update tid tracking (record new, garbage collect old)
        state.update_tids(current_tids)
        t_update = time.monotonic()

        def log(msg):
            print(msg)

        if not vcpu_threads:
            if state.prev_vcpu_count > 0:
                log("vCPU threads: 0")
                log("All pool cores now available for system")
                reset, bad = state.restore_all_affinities(current_tids)
                if reset > 0 or bad > 0:
                    log(f"Restored original affinity for {reset} processes ({bad=})")
                state.prev_vcpu_count = 0
                state.prev_allocated = set()
                state.allocated_vcpu_cores = set()
        else:
            # Pin vCPU threads
            allocated = pin_vcpu_threads(state, vcpu_threads, args.dry_run, log)
            state.set_allocated_cores(allocated)

            # Report changes
            if state.check_vcpu_change(len(vcpu_threads)):
                log(f"vCPU threads: {len(vcpu_threads)} "
                    f"cores: {format_cpu_range(allocated)}  "
                    f"system cores: {format_cpu_range(state.get_system_available())}")
                if len(vcpu_threads) > len(vcpu_pool):
                    log(f"WARNING: {len(vcpu_threads)} vCPUs but only {len(vcpu_pool)} "
                        f"pool cores - vCPUs will share cores")

            # Adjust system process affinities
            moved, expanded, skipped = adjust_system_affinities(state, current_tids, args.dry_run)
            t_adjust = time.monotonic()

            if moved > 0:
                log(f"Moved {moved} processes away from vCPU cores")
            if expanded > 0:
                log(f"Expanded affinity for {expanded} processes to freed cores")
            if skipped > 0:
                log(f"Skipped {skipped} tids with unknown original affinity")

        iter_elapsed = time.monotonic() - iter_start
        timing_threshold_sec = args.timing_threshold / 1000.0
        if timing_threshold_sec == 0 or iter_elapsed > timing_threshold_sec:
            scan_ms = (t_scan - iter_start) * 1000
            update_ms = (t_update - t_scan) * 1000
            if vcpu_threads:
                adjust_ms = (t_adjust - t_update) * 1000
                log(f"Iteration took {iter_elapsed * 1000:.1f}ms "
                    f"(scan={scan_ms:.1f}, update={update_ms:.1f}, adjust={adjust_ms:.1f})")
            else:
                log(f"Iteration took {iter_elapsed * 1000:.1f}ms "
                    f"(scan={scan_ms:.1f}, update={update_ms:.1f})")

        if args.once:
            break

        time.sleep(args.interval)

    return 0


if __name__ == '__main__':
    sys.exit(main())
