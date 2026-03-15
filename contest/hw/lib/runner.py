# SPDX-License-Identifier: GPL-2.0

"""Test execution engine with crash-safe progress tracking."""

import json
import os
import re
import subprocess
import time

from lib.nipa import has_crash


def find_newest_unseen(tests_dir):
    """Scan tests_dir for subdirectories without .seen file.

    Returns the newest one (by mtime) or None.
    """
    if not os.path.isdir(tests_dir):
        return None

    candidates = []
    for entry in os.listdir(tests_dir):
        full = os.path.join(tests_dir, entry)
        if not os.path.isdir(full):
            continue
        if os.path.exists(os.path.join(full, '.seen')):
            continue
        candidates.append((os.path.getmtime(full), full))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def mark_all_seen(tests_dir):
    """Touch .seen file in every subdirectory of tests_dir."""
    if not os.path.isdir(tests_dir):
        return

    for entry in os.listdir(tests_dir):
        full = os.path.join(tests_dir, entry)
        if not os.path.isdir(full):
            continue
        seen_path = os.path.join(full, '.seen')
        if not os.path.exists(seen_path):
            with open(seen_path, 'w', encoding='utf-8') as fp:
                fp.write('')


def load_attempted(test_dir):
    """Load .attempted file (JSON list of test names already tried).

    Returns empty list if file doesn't exist or is corrupt (fresh run).
    """
    path = os.path.join(test_dir, '.attempted')
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as fp:
                return json.load(fp)
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def mark_attempted(test_dir, test_name):
    """Append test_name to .attempted file atomically.

    Write to a temp file, fsync, then rename (atomic on POSIX
    same-filesystem). Called BEFORE starting each test so that if the
    kernel crashes, this test will be skipped on resume (not retried).
    """
    attempted = load_attempted(test_dir)
    attempted.append(test_name)
    path = os.path.join(test_dir, '.attempted')
    tmp_path = path + '.tmp'
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    with os.fdopen(fd, 'w') as fp:
        json.dump(attempted, fp)
        fp.flush()
        os.fsync(fp.fileno())
    os.rename(tmp_path, path)


def _list_tests(test_dir):
    """List available tests from the installed kselftest layout.

    Reads kselftest-list.txt if present, otherwise scans for
    run_kselftest.sh test listing.
    """
    list_file = os.path.join(test_dir, 'kselftest-list.txt')
    if os.path.exists(list_file):
        tests = []
        with open(list_file, encoding='utf-8') as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                # Format: target:test_name
                parts = line.split(':', 1)
                if len(parts) == 2:
                    tests.append((parts[0].strip(), parts[1].strip()))
                else:
                    tests.append(('unknown', parts[0].strip()))
        return tests

    # Fallback: run the listing command
    ret = subprocess.run(
        ['./run_kselftest.sh', '--list'],
        cwd=test_dir, capture_output=True, timeout=30, check=False
    )
    if ret.returncode != 0:
        return []

    tests = []
    for line in ret.stdout.decode('utf-8', 'ignore').strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split(':', 1)
        if len(parts) == 2:
            tests.append((parts[0].strip(), parts[1].strip()))
    return tests


def _namify(what):
    """Convert test name to a safe identifier."""
    if not what:
        return "no-name"
    name = re.sub(r'[^0-9a-zA-Z]+', '-', what)
    if name and name[-1] == '-':
        name = name[:-1]
    return name


def run_tests(test_dir, results_dir):
    """Execute kselftest in 'installed' form.

    For each test:
      1. Check dmesg for crash from previous test -- if found, stop
      2. Check if test_name is in .attempted -- if so, skip (crash recovery)
      3. Write test_name to .attempted + fsync (crash-safe bookkeeping)
      4. Run via: ./run_kselftest.sh -t <target>:<test>
      5. Capture stdout/stderr, save to results_dir/<dir_name>/
      6. Drain dmesg output produced during the test, save to dmesg file
      7. Save metadata (retcode, time, target, prog) to info file

    Returns True if a kernel crash was detected, False otherwise.
    """
    tests = _list_tests(test_dir)
    if not tests:
        print("No tests found")
        return False

    print(f"Found {len(tests)} tests")

    previously_attempted = set(load_attempted(test_dir))
    for test_name in previously_attempted:
        print(f"Skipping previously attempted (crashed): {test_name}")

    # Open dmesg once, drain boot messages
    dmesg = DmesgReader()
    boot_lines = dmesg.drain()
    if boot_lines:
        boot_path = os.path.join(results_dir, 'boot-dmesg')
        with open(boot_path, 'w', encoding='utf-8') as fp:
            fp.write(boot_lines)
        print(f"Saved {len(boot_lines.splitlines())} lines of boot dmesg")

    crashed = has_crash(boot_lines) if boot_lines else False
    if crashed:
        print("Kernel crash detected during boot")

    for test_idx, (target, prog) in enumerate(tests):
        test_name = f"{target}:{prog}"
        safe_name = _namify(prog)
        dir_name = f"{test_idx}-{safe_name}"

        if test_name in previously_attempted:
            continue

        # Check for crash from a previous test before starting a new one
        if crashed:
            print(f"[{test_idx+1}/{len(tests)}] Skipping {test_name}: "
                  "crash detected in previous test")
            continue

        print(f"[{test_idx+1}/{len(tests)}] Running {test_name}")

        # Mark as attempted before execution
        mark_attempted(test_dir, test_name)

        # Create output directory
        test_results_dir = os.path.join(results_dir, dir_name)
        os.makedirs(test_results_dir, exist_ok=True)

        # Run the test
        t1 = time.monotonic()
        try:
            ret = subprocess.run(
                ['./run_kselftest.sh', '-t', f'{target}:{prog}'],
                cwd=test_dir,
                capture_output=True,
                timeout=600,
                check=False
            )
            retcode = ret.returncode
            stdout = ret.stdout.decode('utf-8', 'ignore')
            stderr = ret.stderr.decode('utf-8', 'ignore')
            if not stdout and not stderr:
                print(f"[{test_idx+1}/{len(tests)}] {test_name}: "
                      f"no output (rc={retcode})")
        except subprocess.TimeoutExpired:
            retcode = 1
            stdout = ''
            stderr = 'test timed out'
            print(f"[{test_idx+1}/{len(tests)}] {test_name}: timed out")
        t2 = time.monotonic()
        elapsed = round(t2 - t1, 1)

        # Drain dmesg produced during this test
        test_dmesg = dmesg.drain()
        if test_dmesg:
            with open(os.path.join(test_results_dir, 'dmesg'), 'w',
                      encoding='utf-8') as fp:
                fp.write(test_dmesg)
            if has_crash(test_dmesg):
                crashed = True
                print(f"[{test_idx+1}/{len(tests)}] {test_name}: "
                      "kernel crash detected in dmesg")

        # Save output and metadata
        with open(os.path.join(test_results_dir, 'stdout'), 'w', encoding='utf-8') as fp:
            fp.write(stdout)
        with open(os.path.join(test_results_dir, 'stderr'), 'w', encoding='utf-8') as fp:
            fp.write(stderr)
        with open(os.path.join(test_results_dir, 'info'), 'w', encoding='utf-8') as fp:
            json.dump({'retcode': retcode, 'time': elapsed,
                       'target': target, 'prog': prog}, fp)

        print(f"[{test_idx+1}/{len(tests)}] {test_name}: rc={retcode} ({elapsed}s)")

    dmesg.close()
    return crashed


class DmesgReader:
    """Non-blocking reader for /dev/kmsg.

    Opens /dev/kmsg once and provides a drain() method that returns
    all lines accumulated since the last drain.  Not threaded — the
    caller is expected to drain between tests.

    /dev/kmsg gives each opener its own read position.  It does not
    support seek(), so we just read from wherever the kernel puts us
    (typically the start of the ring buffer) and drain forward.
    """

    def __init__(self):
        self._fd = None
        try:
            # O_RDONLY | O_NONBLOCK so reads return EAGAIN instead of blocking
            self._fd = os.open('/dev/kmsg', os.O_RDONLY | os.O_NONBLOCK)
        except (PermissionError, FileNotFoundError, OSError) as e:
            print(f"DmesgReader: cannot open /dev/kmsg: {e}")

    def drain(self):
        """Read all available lines from /dev/kmsg.

        Returns accumulated text as a string, or '' if nothing new.
        """
        if self._fd is None:
            return ''

        lines = []
        while True:
            try:
                data = os.read(self._fd, 8192)
            except OSError:
                # EAGAIN = no more data available
                break
            if not data:
                break
            # kmsg format: "priority,sequence,timestamp,-;message\n"
            # Just keep the raw lines — hwksft doesn't parse them.
            lines.append(data.decode('utf-8', 'ignore'))

        return ''.join(lines)

    def close(self):
        """Close the /dev/kmsg fd."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
