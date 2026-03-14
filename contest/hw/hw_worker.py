#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""NIPA HW worker — one-shot on-boot test runner."""

import json
import os
import subprocess
import time

from lib.runner import find_newest_unseen, mark_all_seen, run_tests


TESTS_DIR = '/srv/hw-worker/tests'
RESULTS_DIR = '/srv/hw-worker/results'

# kselftest net.config keys (see drivers/net/README.rst)
_NET_CONFIG_KEYS = ['NETIF', 'LOCAL_V4', 'LOCAL_V6', 'REMOTE_V4', 'REMOTE_V6',
                    'LOCAL_PREFIX_V6', 'REMOTE_TYPE', 'REMOTE_ARGS']


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


def _ensure_link_up(ifname):
    """Bring a network interface up and wait for carrier."""
    ret = subprocess.run(['ip', 'link', 'set', ifname, 'up'],
                         capture_output=True, check=False)
    if ret.returncode != 0:
        stderr = ret.stderr.decode('utf-8', 'ignore').strip()
        raise RuntimeError(f"Failed to bring up {ifname}: {stderr}")

    # Wait for carrier (link partner detected)
    for _ in range(30):
        ret = subprocess.run(['ip', '-json', 'link', 'show', 'dev', ifname],
                             capture_output=True, check=False)
        try:
            info = json.loads(ret.stdout)[0]
            if info.get('operstate', '').upper() == 'UP':
                return
        except (json.JSONDecodeError, IndexError):
            pass
        time.sleep(1)
    print(f"Warning: {ifname} carrier not detected after 30s")


def _ensure_addr(ifname, addr):
    """Add an IP address to an interface if not already present."""
    bare_addr = addr.split('/')[0]
    ret = subprocess.run(['ip', 'addr', 'show', 'dev', ifname],
                         capture_output=True, check=False)
    if bare_addr in ret.stdout.decode():
        return
    if '/' not in addr:
        addr += '/64' if ':' in addr else '/24'
    subprocess.run(['ip', 'addr', 'add', addr, 'dev', ifname], check=True)


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
        _ensure_link_up(netif)
        if env.get('LOCAL_V4'):
            _ensure_addr(netif, env['LOCAL_V4'])
        if env.get('LOCAL_V6'):
            _ensure_addr(netif, env['LOCAL_V6'])

    # Configure peer interface (for loopback / same-machine peers)
    remote_ifname = env.get('REMOTE_IFNAME')
    if remote_ifname:
        _ensure_link_up(remote_ifname)
        if env.get('REMOTE_V4'):
            _ensure_addr(remote_ifname, env['REMOTE_V4'])
        if env.get('REMOTE_V6'):
            _ensure_addr(remote_ifname, env['REMOTE_V6'])

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


def main():
    """Find pending tests, run them, and write results."""
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

    results = run_tests(test_dir, results_dir)

    results_file = os.path.join(results_dir, 'results.json')
    fd = os.open(results_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    with os.fdopen(fd, 'w') as fp:
        json.dump(results, fp)
        fp.flush()
        os.fsync(fp.fileno())

    print(f"Completed {len(results)} tests, results in {results_dir}")


if __name__ == '__main__':
    main()
