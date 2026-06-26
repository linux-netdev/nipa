#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""NIPA HW worker — one-shot on-boot test runner."""

import json
import os
import subprocess
import sys
import time

from lib.runner import find_newest_test, run_tests


TESTS_DIR = '/srv/hw-worker/tests'
RESULTS_DIR = '/srv/hw-worker/results'


class _TeeWriter:
    """Write to both the original stream and /dev/kmsg."""

    def __init__(self, original):
        self._original = original
        try:
            self._kmsg = open('/dev/kmsg', 'w', encoding='utf-8',
                              errors='ignore')
        except (PermissionError, FileNotFoundError, OSError):
            self._kmsg = None

    def write(self, text):
        self._original.write(text)
        if self._kmsg and text.strip():
            # kmsg prepends its own priority/timestamp, just send the text
            try:
                self._kmsg.write(f'nipa-hw-worker: {text}')
                self._kmsg.flush()
            except OSError:
                pass

    def flush(self):
        self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)

# kselftest net.config keys (see drivers/net/README.rst)
_NET_CONFIG_KEYS = ['NETIF', 'LOCAL_V4', 'LOCAL_V6', 'REMOTE_V4', 'REMOTE_V6',
                    'LOCAL_PREFIX_V6', 'REMOTE_TYPE', 'REMOTE_ARGS']

# Variables exported to the environment (not written to net.config)
_ENV_ONLY_KEYS = ['DISRUPTIVE', 'KSFT_MACHINE_SLOW']


def _parse_env_file(path):
    """Parse a simple KEY=VALUE env file."""
    env = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding='utf-8') as fp:
        for line in fp:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, val = line.partition('=')
            if sep:
                env[key.strip()] = val.strip()
    return env


def _ip(args, host=None, netns=None, check=True):
    """Run an ip command, optionally on a remote host or in a netns.

    host:  SSH destination (e.g. root@10.0.0.1) — run via ssh
    netns: namespace name — run via ip -netns
    Neither: run locally.
    """
    if host:
        ret = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=10',
             '-o', 'StrictHostKeyChecking=no',
             '-o', 'BatchMode=yes',
             host, f'ip {args}'],
            capture_output=True, timeout=30, check=False
        )
    elif netns:
        ret = subprocess.run(
            ['ip', '-netns', netns] + args.split(),
            capture_output=True, timeout=30, check=False
        )
    else:
        ret = subprocess.run(
            ['ip'] + args.split(),
            capture_output=True, timeout=30, check=False
        )
    if check and ret.returncode != 0:
        stderr = ret.stderr.decode('utf-8', 'ignore').strip()
        where = host or netns or 'local'
        raise RuntimeError(f"ip {args} failed on {where}: {stderr}")
    return ret


def _ensure_link_up(ifname, **kwargs):
    """Bring a network interface up and wait for carrier."""
    _ip(f'link set {ifname} up', **kwargs)

    for _ in range(30):
        ret = _ip(f'-json link show dev {ifname}', check=False, **kwargs)
        try:
            info = json.loads(ret.stdout)[0]
            if info.get('operstate', '').upper() == 'UP':
                return
        except (json.JSONDecodeError, IndexError):
            pass
        time.sleep(1)
    where = kwargs.get('host') or kwargs.get('netns') or 'local'
    print(f"Warning: {ifname} carrier not detected on {where} after 30s")


def _ensure_addr(ifname, addr, **kwargs):
    """Add an IP address to an interface if not already present."""
    bare_addr = addr.split('/')[0]
    ret = _ip(f'addr show dev {ifname}', check=False, **kwargs)
    if bare_addr in ret.stdout.decode():
        return
    if '/' not in addr:
        addr += '/64' if ':' in addr else '/24'
    _ip(f'addr add {addr} dev {ifname}', **kwargs)


def _install_prefix_route(ifname, prefix, local_v6, **kwargs):
    """
    Install a route on remote with a nexthop so that remote doesn't try
    to resolve the L2 for all the containers, just sends the traffic to us.
    """
    _ip(f'-6 route add {prefix} via {local_v6} dev {ifname}', **kwargs)


def _collect_device_info(ifname):
    """Collect devlink device info for the test interface.

    Returns a dict matching ``devlink -j dev info $dev | jq '.[][]'``
    or None if the info cannot be obtained.
    """
    # Find PCI address via ip link
    ret = subprocess.run(['ip', '-d', '-j', 'link', 'show', 'dev', ifname],
                         capture_output=True, timeout=10, check=False)
    if ret.returncode != 0:
        return None
    try:
        pci_addr = json.loads(ret.stdout)[0].get('parentdev')
    except (json.JSONDecodeError, IndexError):
        return None
    if not pci_addr:
        return None

    devlink_dev = f'pci/{pci_addr}'
    ret = subprocess.run(['devlink', '-j', 'dev', 'info', devlink_dev],
                         capture_output=True, timeout=10, check=False)
    if ret.returncode != 0:
        return None
    try:
        data = json.loads(ret.stdout)
        # Strip outer nests: {"info":{"pci/...": {actual data}}}
        return data['info'][devlink_dev]
    except (json.JSONDecodeError, KeyError):
        return None


def _get_ifindex(netif):
    """Return the ifindex of an interface, or None."""
    try:
        with open(f'/sys/class/net/{netif}/ifindex', encoding='utf-8') as fp:
            return int(fp.read().strip())
    except (OSError, ValueError):
        return None


def _read_combined_channels(netif):
    """Read channel config via ethtool.

    Returns (max_combined, current_combined), or (None, None) on any
    failure (tool missing, no JSON support, no combined channels, etc).
    """
    try:
        ret = subprocess.run(['ethtool', '--json', '-l', netif],
                             capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if ret.returncode != 0:
        return None, None
    try:
        data = json.loads(ret.stdout)[0]
        return data['combined-max'], data['combined']
    except (json.JSONDecodeError, IndexError, KeyError, TypeError):
        return None, None


def _set_combined_channels(netif, count):
    """Set the combined channel count. Returns True on success."""
    try:
        ret = subprocess.run(['ethtool', '-L', netif, 'combined', str(count)],
                             capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return ret.returncode == 0


def _get_napi_irqs(ifindex):
    """Return the NIC's NAPI IRQs (sorted ascending) via ynl, or None.

    ynl reports NAPIs newest-first, so the raw IRQ order is reversed;
    we sort so IRQ-to-CPU mapping is deterministic.
    """
    try:
        ret = subprocess.run(
            ['ynl', '--family', 'netdev', '--dump', 'napi-get',
             '--json', json.dumps({'ifindex': ifindex}), '--output-json'],
            capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if ret.returncode != 0:
        return None
    try:
        napis = json.loads(ret.stdout)
    except json.JSONDecodeError:
        return None
    irqs = [n['irq'] for n in napis if isinstance(n, dict) and 'irq' in n]
    if not irqs:
        return None
    return sorted(irqs)


def _set_irq_affinity(irq, cpu):
    """Pin a single IRQ to a single CPU. Returns True on success."""
    try:
        with open(f'/proc/irq/{irq}/smp_affinity_list', 'w',
                  encoding='utf-8') as fp:
            fp.write(str(cpu))
        return True
    except OSError:
        return False


def _setup_irq_affinity(netif):
    """Spread the NIC's IRQs across CPUs, one IRQ per CPU.

    Bumps the combined channel count to min(max, ncpus) so the driver
    creates enough queues/IRQs, maps each NAPI IRQ to a distinct CPU
    (IRQ 0 -> CPU 0, IRQ 1 -> CPU 1, ...), then restores the original
    channel count.  Best-effort — every step is permissive:

      - can't read/set channels -> log WARN, still try the IRQ mapping
      - can't get IRQs via ynl   -> log ERR, skip the mapping
    """
    ncpus = os.cpu_count() or 1

    # 1. Read channel config and bump combined to min(max, ncpus)
    max_combined, orig_combined = _read_combined_channels(netif)
    if max_combined is None:
        print(f"WARN: could not read channel config for {netif}")
    else:
        target = min(max_combined, ncpus)
        if _set_combined_channels(netif, target):
            print(f"Set {netif} combined channels to {target} "
                  f"(max={max_combined}, cpus={ncpus})")
        else:
            print(f"WARN: could not set combined channels on {netif}")

    # 2. Get NAPI IRQs via ynl and map them to CPUs
    ifindex = _get_ifindex(netif)
    irqs = _get_napi_irqs(ifindex) if ifindex is not None else None
    if irqs is None:
        print(f"ERR: could not get NAPI IRQs for {netif} via ynl")
    else:
        mapped = []
        for cpu, irq in enumerate(irqs):
            if _set_irq_affinity(irq, cpu):
                mapped.append((irq, cpu))
            else:
                print(f"WARN: could not set affinity for IRQ {irq}")
        # Summarize the mapping in one line -- machines with many cores
        # would otherwise print one line per IRQ.
        if len(mapped) == 1:
            print(f"IRQ mapping: IRQ {mapped[0][0]} -> CPU {mapped[0][1]}")
        elif mapped:
            print(f"IRQ mapping: IRQ {mapped[0][0]} -> CPU {mapped[0][1]} ... "
                  f"IRQ {mapped[-1][0]} -> CPU {mapped[-1][1]}")

    # 3. Restore the original combined channel count
    if orig_combined is not None:
        if _set_combined_channels(netif, orig_combined):
            print(f"Restored {netif} combined channels to {orig_combined}")
        else:
            print(f"WARN: could not restore combined channels on {netif}")


def setup_test_interfaces(test_dir):
    """Configure test NICs and write net.config from nic-test.env.

    The hwksft orchestrator deploys nic-test.env with interface names,
    IP addresses, and remote connectivity info.  This function:
      1. Brings up the DUT and peer interfaces
      2. Adds IP addresses if not already configured
      3. Writes drivers/net/net.config for the kselftest framework
    """
    env = _parse_env_file(os.path.join(test_dir, 'nic-test.env'))
    if not env:
        return

    # Configure DUT interface
    netif = env.get('NETIF')
    if netif:
        # Preserve IPv6 addresses across link flaps
        subprocess.run(['sysctl', '-w', f'net.ipv6.conf.{netif}.keep_addr_on_down=1'],
                       capture_output=True, check=False)
        _ensure_link_up(netif)
        if env.get('LOCAL_V4'):
            _ensure_addr(netif, env['LOCAL_V4'])
        if env.get('LOCAL_V6'):
            _ensure_addr(netif, env['LOCAL_V6'])

        # Spread the NIC's IRQs across CPUs for the test run
        try:
            _setup_irq_affinity(netif)
        except Exception as e:  # pylint: disable=broad-except
            print(f"WARN: IRQ affinity setup failed for {netif}: {e}")

    # Configure peer interface
    remote_ifname = env.get('REMOTE_IFNAME')
    remote_type = env.get('REMOTE_TYPE')
    remote_args = env.get('REMOTE_ARGS', '')

    # Build kwargs for _ip/_ensure_* helpers
    peer_kwargs = {}
    if remote_type == 'ssh' and remote_args:
        peer_kwargs['host'] = remote_args
    elif remote_type == 'netns' and remote_args:
        ns = remote_args
        # Create netns and move peer interface into it
        _ip(f'netns add {ns}', check=False)
        _ip(f'link set {remote_ifname} netns {ns}')
        _ip('link set lo up', netns=ns)
        peer_kwargs['netns'] = ns
        print(f"Moved {remote_ifname} to netns {ns}")

    if remote_ifname and peer_kwargs:
        _ensure_link_up(remote_ifname, **peer_kwargs)
        if env.get('REMOTE_V4'):
            _ensure_addr(remote_ifname, env['REMOTE_V4'], **peer_kwargs)
        if env.get('REMOTE_V6'):
            _ensure_addr(remote_ifname, env['REMOTE_V6'], **peer_kwargs)
        if env.get('LOCAL_PREFIX_V6'):
            _install_prefix_route(remote_ifname, env['LOCAL_PREFIX_V6'],
                                  env['LOCAL_V6'], **peer_kwargs)

    # Wait for peer to be reachable
    peer_ip = env.get('REMOTE_V4', '').split('/')[0]
    if peer_ip and netif:
        for attempt in range(15):
            ret = subprocess.run(['ping', '-c', '1', '-W', '1', '-I', netif, peer_ip],
                                 capture_output=True, check=False)
            if ret.returncode == 0:
                print(f"Peer {peer_ip} reachable after {attempt + 1}s")
                break
            time.sleep(1)
        else:
            print(f"Warning: peer {peer_ip} not reachable after 15s")

    # Write net.config for the kselftest framework
    config_lines = []
    for key in _NET_CONFIG_KEYS:
        if env.get(key):
            config_lines.append(f'{key}={env[key]}')

    if config_lines:
        config_content = '\n'.join(config_lines) + '\n'
        for subdir in ['drivers/net', 'drivers/net/hw']:
            config_dir = os.path.join(test_dir, subdir)
            if os.path.isdir(config_dir):
                path = os.path.join(config_dir, 'net.config')
                with open(path, 'w', encoding='utf-8') as fp:
                    fp.write(config_content)
                print(f"Wrote {path}")

    # Export env-only variables (not net.config) for the test framework
    for key in _ENV_ONLY_KEYS:
        if env.get(key):
            os.environ[key] = env[key]


def main():
    """Find pending tests, run them, and write results."""
    sys.stdout = _TeeWriter(sys.stdout)

    # --prep-only <dir>: run interface setup (step 3) from <dir>/nic-test.env
    # and exit. Skips the newest-test scan, kernel-version check, device
    # info collection, and test execution -- useful for testing the prep
    # steps against a hand-crafted nic-test.env.
    if len(sys.argv) >= 2 and sys.argv[1] == '--prep-only':
        if len(sys.argv) != 3:
            print("Usage: hw_worker.py --prep-only <dir>")
            sys.exit(1)
        prep_dir = sys.argv[2]
        env_path = os.path.join(prep_dir, 'nic-test.env')
        if not os.path.exists(env_path):
            print(f"No nic-test.env found in {prep_dir}")
            sys.exit(1)
        print(f"Prep-only: configuring interfaces from {env_path}")
        setup_test_interfaces(prep_dir)
        return

    tests_dir = TESTS_DIR
    results_base = RESULTS_DIR

    test_dir = find_newest_test(tests_dir)
    if test_dir is None:
        print("No outstanding tests found")
        return

    # Verify we booted into the expected test kernel by comparing
    # the deployed kernel version against the running kernel.
    kver_path = os.path.join(test_dir, '.kernel-version')
    if not os.path.exists(kver_path):
        print(test_dir, "No kernel version file, skipping")
        return
    with open(kver_path, encoding='utf-8') as fp:
        expected = fp.read().strip()

    actual = os.uname().release
    # The kernel version includes the git hash and instance name
    # (via CONFIG_LOCALVERSION), so accidental prefix collisions
    # (e.g. "6.1" matching "6.12.0") cannot happen in practice.
    # The '-' separator check is an extra safety measure.
    if actual != expected and not actual.startswith(expected + '-'):
        print(test_dir,
              f"Kernel mismatch: running {actual}, expected {expected}")
        return

    print(test_dir, "Starting tests")

    # Configure test interfaces and write net.config
    setup_test_interfaces(test_dir)

    reservation_id = os.path.basename(test_dir)
    results_dir = os.path.join(results_base, reservation_id)
    os.makedirs(results_dir, exist_ok=True)

    # Collect devlink device info for the test NIC
    env = _parse_env_file(os.path.join(test_dir, 'nic-test.env'))
    netif = env.get('NETIF')
    if netif:
        dev_info = _collect_device_info(netif)
        if dev_info:
            with open(os.path.join(results_dir, 'device-info.json'), 'w',
                      encoding='utf-8') as fp:
                json.dump(dev_info, fp)
            print(f"Collected device info for {netif}")
        else:
            print(f"Warning: could not collect device info for {netif}")

    crashed = run_tests(test_dir, results_dir)

    print(f"Completed, results in {results_dir}")
    if crashed:
        print("NIPA DETECTED SYSTEM CRASH, REBOOT ME PLEASE")


if __name__ == '__main__':
    main()
