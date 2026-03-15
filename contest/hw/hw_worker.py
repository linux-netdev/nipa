#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""NIPA HW worker — one-shot on-boot test runner."""

import json
import os
import subprocess
import sys
import time

from lib.runner import find_newest_unseen, mark_all_seen, run_tests


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
_ENV_ONLY_KEYS = ['DISRUPTIVE']


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

    tests_dir = TESTS_DIR
    results_base = RESULTS_DIR

    test_dir = find_newest_unseen(tests_dir)
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

    mark_all_seen(tests_dir)

    print(test_dir, "Starting tests")

    # Configure test interfaces and write net.config
    setup_test_interfaces(test_dir)

    reservation_id = os.path.basename(test_dir)
    results_dir = os.path.join(results_base, reservation_id)
    os.makedirs(results_dir, exist_ok=True)

    crashed = run_tests(test_dir, results_dir)

    print(f"Completed, results in {results_dir}")
    if crashed:
        print("NIPA DETECTED SYSTEM CRASH, REBOOT ME PLEASE")


if __name__ == '__main__':
    main()
