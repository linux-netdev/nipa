# SPDX-License-Identifier: GPL-2.0

"""Artifact deployment, kexec, kernel build, and crash recovery."""

import json
import os
import random
import re
import shutil
import string
import subprocess
import sys
import time
from dataclasses import dataclass, field

# Add project root for cross-package imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '..'))

from contest.remote.lib.crash import has_crash  # noqa: E402


# Log file handle, set by set_log_file() before builds start.
_log_fp = None

# Cached initramfs path per machine IP, survives across kexec calls
_initrd_cache = {}


@dataclass
class WaitResult:
    """Result of wait_for_results()."""
    ok: bool
    error: str = ''


def set_log_file(fp):
    """Set the file handle for command output logging."""
    global _log_fp  # pylint: disable=global-statement
    _log_fp = fp


def _run(cmd, check=True, capture_output=False, **kwargs):
    """Run a command, logging output to the log file if set."""
    if _log_fp:
        _log_fp.write(f"$ {' '.join(cmd)}\n")
        _log_fp.flush()
    if capture_output:
        return subprocess.run(cmd, capture_output=True, check=check, **kwargs)
    return subprocess.run(cmd, stdout=_log_fp, stderr=_log_fp,
                          check=check, **kwargs)





def build_kernel(config, tree_path):
    """Build kernel on the build node.

    Runs make mrproper, applies base + NIC driver kconfigs, builds with -j.
    """
    _run(['make', '-C', tree_path, 'mrproper'])

    # Apply base defconfig
    _run(['make', '-C', tree_path, 'defconfig'])

    # Apply extra kconfig for the NIC driver
    extra_kconfig = config.get('build', 'extra_kconfig', fallback=None)
    if extra_kconfig:
        _run(['scripts/kconfig/merge_config.sh', '-m', '.config', extra_kconfig],
             cwd=tree_path)
        _run(['make', '-C', tree_path, 'olddefconfig'])

    # Add a random suffix to make the kernel version unique per build
    tag = ''.join(random.choices(string.ascii_lowercase, k=4))
    localversion_path = os.path.join(tree_path, 'localversion')
    with open(localversion_path, 'w', encoding='utf-8') as fp:
        fp.write(f'-{tag}\n')

    ncpus = os.cpu_count() or 1
    _run(['make', '-C', tree_path, f'-j{ncpus}'])

    ret = _run(['make', '-C', tree_path, '--no-print-directory', 'kernelrelease'],
               capture_output=True)
    return ret.stdout.decode().strip()


def build_ksft(config, tree_path):
    """Build kselftests and create installed tarball.

    Makes headers, then builds and installs selftests into a staging
    directory, then creates a tarball.
    """
    targets = config.get('ksft', 'target', fallback='net')
    install_path = os.path.join(tree_path, 'ksft-install')

    # Remove stale files from previous builds
    if os.path.exists(install_path):
        shutil.rmtree(install_path)

    _run(['make', '-C', tree_path, 'headers'])
    _run(['make', '-C', os.path.join(tree_path, 'tools/testing/selftests'),
          'TARGETS=' + targets,
          'INSTALL_PATH=' + install_path,
          'install'])

    # Create tarball
    tarball = os.path.join(tree_path, 'ksft-install.tar.gz')
    _run(['tar', 'czf', tarball, '-C', install_path, '.'])
    return tarball


def deploy_artifacts(_config, machine_ips, reservation_id, nic_info, tree_path,
                     kernel_version):
    """SCP kernel + ksft bundle to test machines.

    Deploys to /srv/hw-worker/tests/$reservation_id/ on each machine.
    Also writes the test runner config file with NIC addressing info.
    """
    remote_dir = f'/srv/hw-worker/tests/{reservation_id}'
    kernel_image = os.path.join(tree_path, 'arch/x86/boot/bzImage')
    ksft_tarball = os.path.join(tree_path, 'ksft-install.tar.gz')

    for ipaddr in machine_ips:
        print(f"deploy: {ipaddr} -> {remote_dir}")
        # Create remote directory
        _ssh(ipaddr, f'mkdir -p {remote_dir}')

        # Copy kernel
        _scp(kernel_image, ipaddr, f'{remote_dir}/bzImage')

        # Copy ksft tarball and extract
        _scp(ksft_tarball, ipaddr, f'{remote_dir}/ksft-install.tar.gz')
        _ssh(ipaddr, f'tar xzf {remote_dir}/ksft-install.tar.gz -C {remote_dir}')

        # Write expected kernel version for hw-worker to verify
        _ssh(ipaddr,
             f"cat > {remote_dir}/.kernel-version << 'HEREDOC'\n{kernel_version}\nHEREDOC")

    # Write test config on the primary machine (first in list)
    if nic_info and machine_ips:
        config_lines = []
        config_lines.append(f'NETIF={nic_info.get("ifname", "")}')
        config_lines.append(f'LOCAL_V4={nic_info.get("ip4addr", "")}')
        config_lines.append(f'LOCAL_V6={nic_info.get("ip6addr", "")}')

        peer = nic_info.get('peer')
        if peer:
            config_lines.append(f'REMOTE_IFNAME={peer.get("ifname", "")}')
            config_lines.append(f'REMOTE_V4={peer.get("ip4addr", "")}')
            config_lines.append(f'REMOTE_V6={peer.get("ip6addr", "")}')
            peer_ip = nic_info.get('peer_machine_ip')
            if peer_ip:
                # Cross-machine: peer is on a different machine
                config_lines.append('REMOTE_TYPE=ssh')
                config_lines.append(f'REMOTE_ARGS=root@{peer_ip}')
            else:
                # Same machine: use netns for the peer
                config_lines.append('REMOTE_TYPE=netns')
                config_lines.append('REMOTE_ARGS=nipa-peer')

        if nic_info.get('disruptive'):
            config_lines.append(f'DISRUPTIVE={nic_info["disruptive"]}')

        config_content = '\n'.join(config_lines) + '\n'
        _ssh(machine_ips[0],
             f"cat > {remote_dir}/nic-test.env << 'HEREDOC'\n{config_content}HEREDOC")


def kexec_machine(config, machine_ips, reservation_id, mc=None):
    """SSH to each machine and kexec into the new kernel."""
    remote_dir = f'/srv/hw-worker/tests/{reservation_id}'
    boot_timeout = config.getint('hw', 'max_kexec_boot_timeout', fallback=300)

    def _refresh():
        if mc:
            mc.reservation_refresh(reservation_id)

    for ipaddr in machine_ips:
        # Use the existing initramfs so LVM/DM can assemble the root FS.
        # On the kexec'd test kernel there's no matching initramfs under
        # /boot, so fall back to the cached path from a previous lookup.
        initrd = _ssh(ipaddr,
                      'ls /boot/initramfs-$(uname -r).img 2>/dev/null || '
                      'ls /boot/initrd.img-$(uname -r) 2>/dev/null || true').strip()
        if initrd:
            _initrd_cache[ipaddr] = initrd
        elif ipaddr in _initrd_cache:
            cached = _initrd_cache[ipaddr]
            if _ssh_retcode(ipaddr, f'test -f {cached}') == 0:
                initrd = cached
                print(f"kexec: {ipaddr} using cached initrd {initrd}")

        kexec_cmd = f'kexec -l {remote_dir}/bzImage --reuse-cmdline'
        if initrd:
            kexec_cmd += f' --initrd={initrd}'
            if initrd != _initrd_cache.get(ipaddr, initrd):
                print(f"kexec: {ipaddr} using initrd {initrd}")
        else:
            print(f"kexec: {ipaddr} no initrd found, booting without")
        _ssh(ipaddr, kexec_cmd)
        print(f"kexec: {ipaddr}: ", kexec_cmd)
        # kexec -e will kill the SSH session, so ignore errors
        _ssh(ipaddr, 'kexec -e', check=False, timeout=5)

    # Wait for machines to come back
    for ipaddr in machine_ips:
        print(f"kexec: waiting for {ipaddr} to come back (timeout {boot_timeout}s)")
        _wait_for_ssh(ipaddr, timeout=boot_timeout, keepalive=_refresh)
        print(f"kexec: {ipaddr} is back")


def grab_hw_worker_journal(ipaddr, results_path, suffix=''):
    """Fetch hw-worker journal from the test machine and save locally."""
    journal = _ssh(ipaddr,
                   'journalctl -u nipa-hw-worker.service -b --no-pager',
                   check=False)
    if journal:
        journal_file = os.path.join(results_path, f'hw-worker-journal{suffix}')
        with open(journal_file, 'w', encoding='utf-8') as fp:
            fp.write(journal)


def grab_sol_logs(mc, machine_ids, results_path, sol_start_ids, suffix=''):
    """Fetch SOL output for the test session and save locally.

    Only fetches lines after sol_start_ids (captured before kexec).
    Paginates until the server returns no more lines.
    """
    for mid in machine_ids:
        sol_file = os.path.join(results_path, f'sol-machine-{mid}{suffix}')

        if mid not in sol_start_ids:
            with open(sol_file, 'w', encoding='utf-8') as fp:
                fp.write('<fail: no start ID>\n')
            continue

        cursor = sol_start_ids[mid]
        with open(sol_file, 'w', encoding='utf-8') as fp:
            while True:
                sol = mc.get_sol_logs(mid, start_id=cursor)
                lines = sol.get('lines', [])
                if not lines:
                    break
                for entry in lines:
                    ts = entry.get('ts', '')
                    line = entry.get('line', '')
                    fp.write(f"{ts} {line}\n")
                new_cursor = sol.get('last_id', cursor)
                if new_cursor == cursor:
                    break
                cursor = new_cursor


CRASH_SENTINEL = "NIPA DETECTED SYSTEM CRASH, REBOOT ME PLEASE"


def wait_for_results(config, mc, reservation_id, machine_ids, machine_ips):
    """Wait for hw-worker service to exit, monitoring SOL for hard crashes.

    Returns WaitResult(ok=True) when service exits cleanly,
    WaitResult(ok=False) on failure/timeout.

    SOL is monitored for crash markers.  If a crash is detected and no
    new SOL output arrives for crash_wait_time seconds, the machine is
    assumed hung and we power-cycle it (this makes the service exit).
    """
    max_test_time = config.getint('hw', 'max_test_time', fallback=3600)
    sol_poll_interval = config.getint('hw', 'sol_poll_interval', fallback=15)
    crash_wait_time = config.getint('hw', 'crash_wait_time', fallback=120)

    start_time = time.monotonic()
    # Seed SOL cursors to current position so we only see new output
    sol_last_ids = {}
    for mid in machine_ids:
        sol = mc.get_sol_logs(mid, limit=1, sort='desc')
        sol_last_ids[mid] = sol.get('last_id', 0)
    crash_detected_at = {}  # machine_id -> monotonic time when crash first seen

    while True:
        elapsed = time.monotonic() - start_time
        if elapsed > max_test_time:
            msg = "max test time exceeded"
            print(f"wait_for_results: {msg}")
            return WaitResult(ok=False, error=msg)

        # Refresh reservation
        result = mc.reservation_refresh(reservation_id)
        if not result.get('ok') and 'error' in result:
            msg = f"reservation refresh failed: {result['error']}"
            print(f"wait_for_results: {msg}")
            return WaitResult(ok=False, error=msg)

        # Check if hw-worker service has exited.
        primary_ip = machine_ips[0]
        state = _ssh(primary_ip,
                     'systemctl show -p ActiveState --value nipa-hw-worker.service',
                     check=False).strip()
        if state == 'activating':
            pass  # still running, continue polling
        elif state == 'failed':
            msg = "hw-worker service failed"
            print(f"wait_for_results: {msg}")
            return WaitResult(ok=False, error=msg)
        elif state in ('inactive', 'active'):
            print("wait_for_results: hw-worker completed")
            return WaitResult(ok=True)

        # Check SOL logs for crashes on each machine
        for i, mid in enumerate(machine_ids):
            ipaddr = machine_ips[i]
            sol = mc.get_sol_logs(mid, start_id=sol_last_ids[mid])
            sol_text = '\n'.join(entry['line'] for entry in sol.get('lines', []))
            new_last_id = sol.get('last_id', sol_last_ids[mid])

            if has_crash(sol_text):
                if mid not in crash_detected_at:
                    crash_detected_at[mid] = time.monotonic()
                    crash_lines = [l for l in sol_text.split('\n')
                                   if any(m in l for m in ('] RIP: ', '] Call Trace:',
                                                           '] ref_tracker: ',
                                                           'unreferenced object 0x'))]
                    for cl in crash_lines:
                        print(f"wait_for_results: crash on machine {mid}: {cl.strip()}")

            if mid in crash_detected_at:
                if new_last_id == sol_last_ids[mid]:
                    # No new SOL output after crash — machine may be hung
                    crash_age = time.monotonic() - crash_detected_at[mid]
                    if crash_age >= crash_wait_time:
                        print(f"wait_for_results: machine {mid} hung, power cycling")
                        mc.power_cycle(mid)
                        power_cycle_timeout = config.getint(
                            'hw', 'max_power_cycle_timeout', fallback=600)
                        _wait_for_ssh(ipaddr, timeout=power_cycle_timeout,
                                      keepalive=lambda: mc.reservation_refresh(reservation_id))
                        # Machine rebooted into default kernel, hw-worker
                        # will see kernel mismatch and exit.  The service
                        # state will flip to inactive/failed, caught on
                        # the next iteration.
                        del crash_detected_at[mid]
                        print(f"wait_for_results: machine {mid} back after power cycle")
                # else: SOL still progressing post-crash, hw-worker may
                # still be running and will detect the crash via dmesg

            sol_last_ids[mid] = new_last_id

        time.sleep(sol_poll_interval)


def _journal_has_crash_sentinel(ipaddr):
    """Check if hw-worker journal contains the crash sentinel."""
    journal = _ssh(ipaddr,
                   'journalctl -u nipa-hw-worker.service -b --no-pager',
                   check=False)
    return CRASH_SENTINEL in journal


def reboot_machine(config, mc, reservation_id, machine_ids, machine_ips):
    """Reboot the machine via SSH, falling back to BMC power cycle."""
    primary_ip = machine_ips[0]
    boot_timeout = config.getint('hw', 'max_kexec_boot_timeout', fallback=300)
    power_cycle_timeout = config.getint('hw', 'max_power_cycle_timeout', fallback=600)

    def _refresh():
        mc.reservation_refresh(reservation_id)

    # Try SSH reboot first
    print(f"reboot_machine: rebooting {primary_ip} via SSH")
    _ssh(primary_ip, 'reboot', check=False, timeout=5)

    try:
        _wait_for_ssh(primary_ip, timeout=boot_timeout, keepalive=_refresh)
        print(f"reboot_machine: {primary_ip} is back")
    except TimeoutError:
        # SSH reboot didn't work, hard cycle via BMC
        print(f"reboot_machine: SSH reboot timed out, power cycling")
        mc.power_cycle(machine_ids[0])
        _wait_for_ssh(primary_ip, timeout=power_cycle_timeout, keepalive=_refresh)
        print(f"reboot_machine: {primary_ip} back after power cycle")


def fetch_results(machine_ips, reservation_id, results_path):
    """SCP test output from the test machine back to the build node.

    Copies the results directory tree and the .attempted file.
    """
    primary_ip = machine_ips[0]
    remote_results = f'/srv/hw-worker/results/{reservation_id}'
    remote_tests = f'/srv/hw-worker/tests/{reservation_id}'

    # Copy the entire results directory tree
    local_results = os.path.join(results_path, 'test-outputs')
    os.makedirs(local_results, exist_ok=True)
    # Use scp -r to grab all test output directories
    ret = subprocess.run(
        ['scp', '-r', '-o', 'StrictHostKeyChecking=no',
         '-o', 'BatchMode=yes',
         f'root@{primary_ip}:{remote_results}/', local_results],
        capture_output=True, timeout=300, check=False
    )
    if ret.returncode != 0:
        print(f"fetch_results: scp failed: {ret.stderr.decode('utf-8', 'ignore')}")

    # Copy .attempted for crash tracking
    _scp_from(primary_ip, f'{remote_tests}/.attempted',
              os.path.join(results_path, 'attempted.json'),
              check=False)


def parse_results(reservation_id, results_path, link):
    """Parse fetched test output into a vmksft-p-style result list.

    Reads info/stdout files from the test-outputs directory and
    the .attempted file to identify crashed tests.
    """
    # Find the actual results subdir (scp -r creates reservation_id/ inside)
    local_results = os.path.join(results_path, 'test-outputs')
    output_dir = os.path.join(local_results, str(reservation_id))
    if not os.path.isdir(output_dir):
        output_dir = local_results

    # Parse each test output directory
    cases = []
    completed_tests = set()
    if os.path.isdir(output_dir):
        for entry in sorted(os.listdir(output_dir)):
            test_dir = os.path.join(output_dir, entry)
            if not os.path.isdir(test_dir):
                continue

            info_path = os.path.join(test_dir, 'info')
            stdout_path = os.path.join(test_dir, 'stdout')

            if not os.path.exists(info_path):
                continue

            with open(info_path, encoding='utf-8') as fp:
                info = json.load(fp)

            retcode = info.get('retcode', 1)
            target = info.get('target', 'unknown')
            prog = info.get('prog', entry)
            test_name = f"{target}:{prog}"
            completed_tests.add(test_name)

            stdout = ''
            if os.path.exists(stdout_path):
                with open(stdout_path, encoding='utf-8') as fp:
                    stdout = fp.read()

            # Determine result
            result = 'pass'
            if retcode == 4:
                result = 'skip'
            elif retcode != 0:
                result = 'fail'
            if 'ok' not in stdout.lower() and result == 'pass':
                result = 'skip'

            safe_name = re.sub(r'[^0-9a-zA-Z]+', '-', prog)
            if safe_name and safe_name[-1] == '-':
                safe_name = safe_name[:-1]

            outcome = {
                'test': safe_name or entry,
                'group': f'selftests-{re.sub(r"[^0-9a-zA-Z]+", "-", target).rstrip("-")}',
                'result': result,
                'link': link,
            }
            if 'time' in info:
                outcome['time'] = info['time']
            cases.append(outcome)

    # Check .attempted for crashed tests (attempted but no output)
    attempted_path = os.path.join(results_path, 'attempted.json')
    if os.path.exists(attempted_path):
        with open(attempted_path, encoding='utf-8') as fp:
            try:
                attempted = json.load(fp)
            except (json.JSONDecodeError, ValueError):
                attempted = []

        for test_name in attempted:
            if test_name not in completed_tests:
                cases.append({
                    'test': test_name,
                    'group': 'selftests-hw',
                    'result': 'fail',
                    'link': link,
                    'crashes': ['kernel crash during test'],
                })

    return cases


# --- SSH/SCP helpers ---

# StrictHostKeyChecking=no: machines are on a private management network
# and may be re-imaged, changing host keys. The risk of MITM on a
# dedicated management VLAN is accepted.

def _log(msg):
    """Write a line to the log file if set."""
    if _log_fp:
        _log_fp.write(msg)
        _log_fp.flush()


def _ssh(ipaddr, cmd, check=True, timeout=30):
    """Run a command on a remote machine via SSH."""
    _log(f"$ ssh {cmd}\n")
    try:
        ret = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=10',
             '-o', 'StrictHostKeyChecking=no',
             '-o', 'BatchMode=yes',
             f'root@{ipaddr}', cmd],
            capture_output=True, timeout=timeout, check=False
        )
        stdout = ret.stdout.decode('utf-8', 'ignore')
        stderr = ret.stderr.decode('utf-8', 'ignore')
        if stdout:
            _log(stdout)
        if stderr:
            _log(f"(stderr) {stderr}")
        if ret.returncode != 0:
            _log(f"(rc={ret.returncode})\n")
            if check:
                raise RuntimeError(f"SSH to {ipaddr} failed: {stderr}")
        return stdout
    except subprocess.TimeoutExpired:
        _log("(timeout)\n")
        if check:
            raise
        return ''


def _ssh_retcode(ipaddr, cmd, timeout=30):
    """Run SSH command and return the exit code."""
    try:
        ret = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=10',
             '-o', 'StrictHostKeyChecking=no',
             '-o', 'BatchMode=yes',
             f'root@{ipaddr}', cmd],
            capture_output=True, timeout=timeout, check=False
        )
        return ret.returncode
    except subprocess.TimeoutExpired:
        return 1


def _scp(local_path, remote_ip, remote_path, check=True):
    """Copy a file to a remote machine."""
    _log(f"$ scp {local_path} [remote]:{remote_path}\n")
    ret = subprocess.run(
        ['scp', '-o', 'StrictHostKeyChecking=no',
         '-o', 'BatchMode=yes',
         local_path, f'root@{remote_ip}:{remote_path}'],
        capture_output=True, timeout=300, check=False
    )
    if ret.returncode != 0:
        stderr = ret.stderr.decode('utf-8', 'ignore')
        _log(f"(stderr) {stderr}")
        if check:
            raise RuntimeError(f"SCP to {remote_ip} failed: {stderr}")


def _scp_from(remote_ip, remote_path, local_path, check=True):
    """Copy a file from a remote machine."""
    ret = subprocess.run(
        ['scp', '-o', 'StrictHostKeyChecking=no',
         '-o', 'BatchMode=yes',
         f'root@{remote_ip}:{remote_path}', local_path],
        capture_output=True, timeout=300, check=False
    )
    if check and ret.returncode != 0:
        stderr = ret.stderr.decode('utf-8', 'ignore')
        raise RuntimeError(f"SCP from {remote_ip} failed: {stderr}")


def _wait_for_ssh(ipaddr, timeout=300, interval=10, keepalive=None):
    """Wait for SSH to become available on a machine."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if keepalive:
            keepalive()
        try:
            ret = subprocess.run(
                ['ssh', '-o', 'ConnectTimeout=5',
                 '-o', 'StrictHostKeyChecking=no',
                 '-o', 'BatchMode=yes',
                 f'root@{ipaddr}', 'true'],
                capture_output=True, timeout=10, check=False
            )
            if ret.returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(interval)
    raise TimeoutError(f"SSH to {ipaddr} not available after {timeout}s")
