# SPDX-License-Identifier: GPL-2.0

"""Artifact deployment, kexec, kernel build, and crash recovery."""

import json
import os
import random
import shutil
import string
import subprocess
import tempfile
import time


# Log file handle, set by set_log_file() before builds start.
_log_fp = None

# Cached initramfs path per machine IP, survives across kexec calls
_initrd_cache = {}


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


def _has_crash(output):
    """Check if output contains kernel crash markers.

    Mirrors contest/remote/lib/crash.py:has_crash().
    """
    return (output.find('] RIP: ') != -1 or
            output.find('] Call Trace:') != -1 or
            output.find('] ref_tracker: ') != -1 or
            output.find('unreferenced object 0x') != -1)


def _has_reboot(output):
    """Check if output contains early-boot marker indicating self-reboot."""
    return output.find('[    0.000000]') != -1


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
            config_lines.append('REMOTE_TYPE=ssh')
            peer_ip = nic_info.get('peer_machine_ip', machine_ips[0])
            config_lines.append(f'REMOTE_ARGS=root@{peer_ip}')

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
        print(f"kexec: {ipaddr} loaded, executing")
        # kexec -e will kill the SSH session, so ignore errors
        _ssh(ipaddr, 'kexec -e', check=False, timeout=5)

    # Wait for machines to come back
    for ipaddr in machine_ips:
        print(f"kexec: waiting for {ipaddr} to come back (timeout {boot_timeout}s)")
        _wait_for_ssh(ipaddr, timeout=boot_timeout, keepalive=_refresh)
        print(f"kexec: {ipaddr} is back")


def wait_for_results(config, mc, reservation_id, machine_ids, machine_ips,
                     results_path=None):
    """Main wait loop with crash monitoring.

    Polls SOL logs via mc.get_sol_logs() to detect crashes.
    On crash: waits crash_wait_time, power cycles,
    re-kexecs, lets hw-worker resume remaining tests.
    """
    max_test_time = config.getint('hw', 'max_test_time', fallback=3600)
    sol_poll_interval = config.getint('hw', 'sol_poll_interval', fallback=15)
    crash_wait_time = config.getint('hw', 'crash_wait_time', fallback=120)
    boot_timeout = config.getint('hw', 'max_kexec_boot_timeout', fallback=300)

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
            # Caller (hwksft.test) will still try fetch_results, which
            # handles missing results gracefully.
            print("wait_for_results: max test time exceeded")
            break

        # Refresh reservation
        result = mc.reservation_refresh(reservation_id)
        if not result.get('ok') and 'error' in result:
            print(f"wait_for_results: reservation refresh failed: {result['error']}")
            break

        # Check if hw-worker has produced results on primary machine
        primary_ip = machine_ips[0]
        ret = _ssh_retcode(primary_ip,
                            f'test -f /srv/hw-worker/results/{reservation_id}/results.json')
        if ret == 0:
            print("wait_for_results: hw-worker completed")
            return True

        # Check if hw-worker exited without producing results
        ret = _ssh_retcode(primary_ip, 'systemctl is-active nipa-hw-worker.service')
        if ret != 0:
            # Service is inactive/failed — no results.json means it failed
            print("wait_for_results: hw-worker exited without results")
            return False

        # Check SOL logs for crashes on each machine
        for i, mid in enumerate(machine_ids):
            ipaddr = machine_ips[i]
            sol = mc.get_sol_logs(mid, start_id=sol_last_ids[mid])
            sol_text = '\n'.join(entry['line'] for entry in sol.get('lines', []))
            new_last_id = sol.get('last_id', sol_last_ids[mid])

            if _has_crash(sol_text):
                if mid not in crash_detected_at:
                    crash_detected_at[mid] = time.monotonic()
                    # Find and log the specific crash lines
                    crash_lines = [l for l in sol_text.split('\n')
                                   if any(m in l for m in ('] RIP: ', '] Call Trace:',
                                                           '] ref_tracker: ',
                                                           'unreferenced object 0x'))]
                    for cl in crash_lines:
                        print(f"wait_for_results: crash on machine {mid}: {cl.strip()}")
                    if results_path:
                        crash_file = os.path.join(results_path, f'crash-machine-{mid}')
                        with open(crash_file, 'a', encoding='utf-8') as fp:
                            fp.write(sol_text + '\n')

            if mid in crash_detected_at:
                if _has_reboot(sol_text):
                    # Machine is already rebooting itself, skip power cycle
                    print(f"wait_for_results: self-reboot on machine {mid}")
                    _crash_recover(config, mc, mid, ipaddr,
                                   reservation_id, boot_timeout,
                                   skip_power_cycle=True)
                    del crash_detected_at[mid]
                elif new_last_id == sol_last_ids[mid]:
                    # No new SOL output after crash
                    crash_age = time.monotonic() - crash_detected_at[mid]
                    if crash_age >= crash_wait_time:
                        print(f"wait_for_results: recovering machine {mid}")
                        _crash_recover(config, mc, mid, ipaddr,
                                       reservation_id, boot_timeout)
                        del crash_detected_at[mid]
                # else: SOL still progressing post-crash, keep waiting

            sol_last_ids[mid] = new_last_id

        time.sleep(sol_poll_interval)


def _crash_recover(config, mc, machine_id, ipaddr, reservation_id,
                   boot_timeout, skip_power_cycle=False):
    """Recover a machine after a kernel crash.

    1. Power cycle (boots into default kernel) — skipped if machine
       is already rebooting itself (self-reboot detected in SOL).
    2. Wait for SSH
    3. Re-kexec into test kernel

    This is intentionally synchronous and blocks the SOL poll loop:
    recovery takes several minutes, and the poll loop needs the machine
    back before it can continue monitoring anyway.

    No explicit coordination with hw-worker is needed: on the default
    kernel hw-worker sees a version mismatch and exits immediately.
    The subsequent kexec kills everything on the machine anyway, so
    even if hw-worker is still running its check when kexec arrives,
    the outcome is the same.
    """
    def _refresh():
        mc.reservation_refresh(reservation_id)

    if not skip_power_cycle:
        print(f"crash_recover: power cycling machine {machine_id}")
        mc.power_cycle(machine_id)
        # Power cycle goes through BMC + BIOS POST, takes much longer than kexec
        power_cycle_timeout = config.getint('hw', 'max_power_cycle_timeout', fallback=600)
        print(f"crash_recover: waiting for SSH on {ipaddr} (timeout {power_cycle_timeout}s)")
        _wait_for_ssh(ipaddr, timeout=power_cycle_timeout, keepalive=_refresh)
    else:
        print(f"crash_recover: waiting for SSH on {ipaddr} after self-reboot (timeout {boot_timeout}s)")
        _wait_for_ssh(ipaddr, timeout=boot_timeout, keepalive=_refresh)

    print(f"crash_recover: SSH is back on {ipaddr}, re-kexecing")
    kexec_machine(config, [ipaddr], reservation_id, mc=mc)


def fetch_results(_config, machine_ips, reservation_id, rinfo):
    """SCP results from test machines back to build node.

    Parse and format into vmksft-p-style result list.
    Tests that crashed (in .attempted but not in results) are marked
    as result='fail' with crash info.
    """
    primary_ip = machine_ips[0]
    remote_results = f'/srv/hw-worker/results/{reservation_id}'

    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy results.json
        _scp_from(primary_ip, f'{remote_results}/results.json',
                  os.path.join(tmpdir, 'results.json'))

        # Copy .attempted for crash tracking
        remote_tests = f'/srv/hw-worker/tests/{reservation_id}'
        _scp_from(primary_ip, f'{remote_tests}/.attempted',
                  os.path.join(tmpdir, 'attempted.json'),
                  check=False)

        # Parse results
        results_path = os.path.join(tmpdir, 'results.json')
        if os.path.exists(results_path):
            with open(results_path, encoding='utf-8') as fp:
                raw_results = json.load(fp)
        else:
            raw_results = []

        # Load attempted tests
        attempted_path = os.path.join(tmpdir, 'attempted.json')
        attempted = []
        if os.path.exists(attempted_path):
            with open(attempted_path, encoding='utf-8') as fp:
                attempted = json.load(fp)

        # Identify crashed tests: in attempted but not in results
        result_names = {r['test'] for r in raw_results}
        link = rinfo.get('link', '')

        cases = []
        for r in raw_results:
            outcome = {
                'test': r['test'],
                'group': r.get('group', 'selftests-hw'),
                'result': r['result'],
                'link': link,
            }
            for key in ['time', 'crashes']:
                if key in r:
                    outcome[key] = r[key]
            cases.append(outcome)

        for test_name in attempted:
            if test_name not in result_names:
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

def _ssh(ipaddr, cmd, check=True, timeout=30):
    """Run a command on a remote machine via SSH."""
    try:
        ret = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=10',
             '-o', 'StrictHostKeyChecking=no',
             '-o', 'BatchMode=yes',
             f'root@{ipaddr}', cmd],
            capture_output=True, timeout=timeout, check=False
        )
        if check and ret.returncode != 0:
            stderr = ret.stderr.decode('utf-8', 'ignore')
            raise RuntimeError(f"SSH to {ipaddr} failed: {stderr}")
        return ret.stdout.decode('utf-8', 'ignore')
    except subprocess.TimeoutExpired:
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
    ret = subprocess.run(
        ['scp', '-o', 'StrictHostKeyChecking=no',
         '-o', 'BatchMode=yes',
         local_path, f'root@{remote_ip}:{remote_path}'],
        capture_output=True, timeout=300, check=False
    )
    if check and ret.returncode != 0:
        stderr = ret.stderr.decode('utf-8', 'ignore')
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
