# SPDX-License-Identifier: GPL-2.0

"""Artifact deployment, kexec, kernel build, and crash recovery."""

import json
import os
import random
import re
import shutil
import string
import subprocess
import time
from dataclasses import dataclass, field

from lib.nipa import (has_crash, extract_crash, guess_indicators,
                      result_from_indicators, parse_nested_tests, namify)


# Log file handle, set by set_log_file() before builds start.
_log_fp = None

# Cached initramfs path per machine IP, survives across kexec calls
_initrd_cache = {}


@dataclass
class WaitResult:
    """Result of wait_for_results()."""
    ok: bool
    error: str = ''
    needs_power_cycle: bool = False


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

    # Apply extra kconfig fragments (space-separated list)
    extra_kconfig = config.get('build', 'extra_kconfig', fallback=None)
    if extra_kconfig:
        configs = extra_kconfig.split()
        _run(['scripts/kconfig/merge_config.sh', '-m', '.config'] + configs,
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
                     kernel_version, filters=None):
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

        # Deploy crash filters if available
        if filters:
            import tempfile as _tmpfile
            with _tmpfile.NamedTemporaryFile(mode='w', suffix='.json',
                                             delete=False) as fp:
                json.dump(filters, fp)
                tmp_path = fp.name
            try:
                _scp(tmp_path, ipaddr, f'{remote_dir}/filters.json')
            finally:
                os.unlink(tmp_path)

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
    crash_wait_time = config.getint('hw', 'crash_wait_time', fallback=600)

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
                        print(f"wait_for_results: machine {mid} hung")
                        return WaitResult(ok=False,
                                          error='machine hung after crash',
                                          needs_power_cycle=True)
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


def check_healthy_ssh(ipaddr):
    """Check if a machine is reachable via SSH. Returns True if healthy."""
    return _ssh_retcode(ipaddr, 'true', timeout=10) == 0


def reboot_machine(config, mc, reservation_id, machine_ids, machine_ips):
    """Reboot the machine via SSH, falling back to BMC power cycle."""
    primary_ip = machine_ips[0]
    power_cycle_timeout = config.getint('hw', 'max_power_cycle_timeout', fallback=600)

    def _refresh():
        mc.reservation_refresh(reservation_id)

    # Check if SSH is responsive at all before trying reboot
    if check_healthy_ssh(primary_ip):
        print(f"reboot_machine: rebooting {primary_ip} via SSH")
        _ssh(primary_ip, 'reboot', check=False, timeout=5)
        # Wait for the machine to actually go down before checking
        # if it's back. Without this delay, _wait_for_ssh may succeed
        # immediately because the machine hasn't shut down yet.
        time.sleep(10)
        # Verify the machine actually went down
        probe = _ssh_retcode(primary_ip, 'true', timeout=5)
        if probe == 0:
            print(f"reboot_machine: WARNING: {primary_ip} still responsive "
                  "after reboot, waiting longer")
            time.sleep(30)
        try:
            _wait_for_ssh(primary_ip, timeout=power_cycle_timeout, keepalive=_refresh)
            print(f"reboot_machine: {primary_ip} is back")
            return
        except TimeoutError:
            print(f"reboot_machine: SSH reboot timed out, falling back to BMC")
    else:
        print(f"reboot_machine: SSH not responsive on {primary_ip}")

    # BMC power cycle
    print(f"reboot_machine: power cycling {primary_ip} via BMC")
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

    # Copy the entire results directory tree.
    # Use rsync to put contents directly into test-outputs/ without
    # creating a reservation_id subdirectory.
    local_results = os.path.join(results_path, 'test-outputs')
    os.makedirs(local_results, exist_ok=True)
    ret = subprocess.run(
        ['rsync', '-a', '-e',
         'ssh -o StrictHostKeyChecking=no -o BatchMode=yes',
         f'root@{primary_ip}:{remote_results}/', local_results + '/'],
        capture_output=True, timeout=300, check=False
    )
    if ret.returncode != 0:
        print(f"fetch_results: scp failed: {ret.stderr.decode('utf-8', 'ignore')}")

    # Copy .attempted for crash tracking
    _scp_from(primary_ip, f'{remote_tests}/.attempted',
              os.path.join(results_path, 'attempted.json'),
              check=False)


def parse_results(results_path, link):
    """Parse fetched test output into a vmksft-p-style result list.

    Reads info/stdout files from the test-outputs directory and
    the .attempted file to identify crashed tests.
    """
    output_dir = os.path.join(results_path, 'test-outputs')

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

            # Determine result using indicators
            indicators = guess_indicators(stdout)
            result = result_from_indicators(retcode, indicators)

            # Parse nested subtests
            nested = parse_nested_tests(stdout, namify)

            # Determine retry result if present
            retry_result = None
            retry_nested = None
            if 'retry_retcode' in info:
                retry_stdout = ''
                retry_dir = os.path.join(output_dir, entry + '-retry')
                retry_stdout_path = os.path.join(retry_dir, 'stdout')
                if os.path.exists(retry_stdout_path):
                    with open(retry_stdout_path, encoding='utf-8') as fp:
                        retry_stdout = fp.read()
                retry_indicators = guess_indicators(retry_stdout)
                retry_result = result_from_indicators(info['retry_retcode'],
                                                      retry_indicators)
                if nested:
                    retry_nested = list(nested)
                    parse_nested_tests(retry_stdout, namify,
                                       prev_results=retry_nested)

            safe_name = namify(prog)

            outcome = {
                'test': safe_name or entry,
                'group': f'selftests-{namify(target)}',
                'result': result,
                'link': link,
            }
            if 'time' in info:
                outcome['time'] = info['time']
            if retry_result is not None:
                outcome['retry'] = retry_result
            if info.get('crashes'):
                outcome['crashes'] = info['crashes']
            if retry_nested:
                outcome['results'] = retry_nested
            elif nested:
                outcome['results'] = nested
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


def process_crashes(results_path, tree_path, filters):
    """Post-process crash data from test output directories.

    For each test that has a dmesg file with crash markers:
    1. Extract crash lines and compute fingerprints
    2. Decode the crash with scripts/decode_stacktrace.sh
    3. Save decoded output + fingerprints to a crash file
    4. Record crashes in the test's result entry

    Also processes boot-dmesg.
    """
    output_dir = os.path.join(results_path, 'test-outputs')

    all_finger_prints = set()

    # Process boot-dmesg
    boot_dmesg = os.path.join(output_dir, 'boot-dmesg')
    if os.path.exists(boot_dmesg):
        with open(boot_dmesg, encoding='utf-8') as fp:
            dmesg_text = fp.read()
        if has_crash(dmesg_text):
            fps = _decode_and_save_crash(dmesg_text, output_dir, 'boot-crash',
                                         tree_path, filters)
            all_finger_prints.update(fps)

    # Process per-test dmesg files
    if os.path.isdir(output_dir):
        for entry in sorted(os.listdir(output_dir)):
            test_dir = os.path.join(output_dir, entry)
            dmesg_path = os.path.join(test_dir, 'dmesg')
            if not os.path.isdir(test_dir) or not os.path.exists(dmesg_path):
                continue
            with open(dmesg_path, encoding='utf-8') as fp:
                dmesg_text = fp.read()
            if has_crash(dmesg_text):
                fps = _decode_and_save_crash(dmesg_text, test_dir, 'crash',
                                             tree_path, filters)
                all_finger_prints.update(fps)

    return all_finger_prints


def _decode_and_save_crash(dmesg_text, out_dir, filename, tree_path, filters):
    """Extract, decode, and save crash data from dmesg text.

    Returns the set of fingerprints found.
    """
    crash_lines, finger_prints = extract_crash(dmesg_text, '', lambda: filters)

    if not crash_lines:
        return finger_prints

    # Try to decode with scripts/decode_stacktrace.sh
    decoded = '\n'.join(crash_lines)
    decode_script = os.path.join(tree_path, 'scripts', 'decode_stacktrace.sh')
    vmlinux = os.path.join(tree_path, 'vmlinux')
    if os.path.exists(decode_script) and os.path.exists(vmlinux):
        try:
            proc = subprocess.Popen(
                [decode_script, vmlinux, 'auto', './'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=tree_path
            )
            stdout, _stderr = proc.communicate(
                '\n'.join(crash_lines).encode('utf-8'), timeout=30)
            decoded = stdout.decode('utf-8', 'ignore')
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"Warning: decode_stacktrace failed: {e}")

    crash_file = os.path.join(out_dir, filename)
    with open(crash_file, 'a', encoding='utf-8') as fp:
        fp.write("======================================\n")
        fp.write(decoded)
        fp.write("\n\nFinger prints:\n" + "\n".join(finger_prints) + "\n")

    return finger_prints


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
