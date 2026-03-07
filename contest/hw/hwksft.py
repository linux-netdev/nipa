#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

"""NIPA HW kselftest orchestrator service."""

import datetime
import os
import shutil
import subprocess
import sys
import time

# Add the project root to path for cross-package imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

# pylint: disable=wrong-import-position,wrong-import-order
# Imports below require sys.path manipulation for cross-package access.

from core import NipaLifetime  # noqa: E402  # pylint: disable=import-error

from contest.remote.lib.cbarg import CbArg  # noqa: E402
from contest.remote.lib.fetcher import Fetcher  # noqa: E402

from lib.mc_client import MCClient, resolve_machines, resolve_nic_id  # noqa: E402
from lib.deployer import (build_kernel, build_ksft, deploy_artifacts,  # noqa: E402
                          kexec_machine, wait_for_results, fetch_results,
                          set_log_file)

# Config:
#
# [executor]
# name=hwksft-nic0
# group=selftests-hw
# init=force / continue / next
# [remote]
# branches=https://url-to-branches-manifest
# [local]
# base_path=/common/path
# json_path=base-relative/path/to/json
# results_path=base-relative/path/to/raw/outputs
# tree_path=/root-path/to/kernel/git
# patches_path=/root-path/to/patches/dir
# [www]
# url=https://url-to-reach-base-path
# [hw]
# nic_vendor=Intel
# nic_model=E810-C
# machine_control_url=http://control-node:5050
# reservation_retry_time=60
# max_kexec_boot_timeout=300
# max_test_time=3600
# crash_wait_time=120
# sol_poll_interval=15
# [build]
# extra_kconfig=/path/to/nic-driver.config
# [ksft]
# target=net


def test(binfo, rinfo, cbarg):  # pylint: disable=unused-argument
    """Fetcher callback: build, deploy, run, and collect HW test results."""
    print("Run at", datetime.datetime.now())
    cbarg.refresh_config()
    config = cbarg.config

    results_path = os.path.join(config.get('local', 'base_path'),
                                config.get('local', 'results_path'),
                                rinfo['run-cookie'])
    os.makedirs(results_path, exist_ok=True)

    link = config.get('www', 'url') + '/' + \
           config.get('local', 'results_path') + '/' + \
           rinfo['run-cookie']
    rinfo['link'] = link
    grp_name = config.get('executor', 'group', fallback='selftests-hw')

    tree_path = config.get('local', 'tree_path')
    mc_url = config.get('hw', 'machine_control_url')
    nic_vendor = config.get('hw', 'nic_vendor')
    nic_model = config.get('hw', 'nic_model')
    mc = MCClient(mc_url)

    # 1. Build kernel + ksft
    try:
        with open(os.path.join(results_path, 'build'), 'w', encoding='utf-8') as fp:
            set_log_file(fp)
            kernel_version = build_kernel(config, tree_path)
            set_log_file(None)

        # Copy .config for reference
        shutil.copy2(os.path.join(tree_path, '.config'),
                      os.path.join(results_path, 'config'))

        with open(os.path.join(results_path, 'ksft-build'), 'w', encoding='utf-8') as fp:
            set_log_file(fp)
            build_ksft(config, tree_path)
            set_log_file(None)
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"Build failed: {e}")
        set_log_file(None)
        return [{
            'test': 'build',
            'group': grp_name,
            'result': 'fail',
            'link': link,
        }]

    # 2. Resolve machines for NIC
    all_nics = mc.get_nic_info()
    nic_id = resolve_nic_id(all_nics, nic_vendor, nic_model)
    machine_ids, nic = resolve_machines(all_nics, nic_id)

    # Build nic_info dict with peer info for deployment
    nic_deploy_info = {
        'ifname': nic.get('ifname', ''),
        'ip4addr': nic.get('ip4addr', ''),
        'ip6addr': nic.get('ip6addr', ''),
    }
    if nic.get('peer_id'):
        for n in all_nics:
            if n['id'] == nic['peer_id']:
                nic_deploy_info['peer'] = {
                    'ifname': n.get('ifname', ''),
                    'ip4addr': n.get('ip4addr', ''),
                    'ip6addr': n.get('ip6addr', ''),
                }
                break

    # 3. Get machine IPs for SSH/SCP
    all_machines = mc.get_machine_info()
    machine_ip_map = {m['id']: m['mgmt_ipaddr'] for m in all_machines}
    machine_ips = [machine_ip_map[mid] for mid in machine_ids]

    # Record peer machine IP so deployer can set REMOTE_ARGS
    if nic.get('peer_id'):
        for n in all_nics:
            if n['id'] == nic['peer_id']:
                nic_deploy_info['peer_machine_ip'] = machine_ip_map.get(
                    n['machine_id'], machine_ips[0])
                break

    # 4. Reserve machines (retry loop with backoff)
    max_retries = config.getint('hw', 'max_reservation_retries', fallback=30)
    retry_time = config.getint('hw', 'reservation_retry_time', fallback=60)
    reservation_id = None
    for attempt in range(max_retries):
        result = mc.reserve(machine_ids)
        if 'reservation_id' in result:
            reservation_id = result['reservation_id']
            break
        wait = min(retry_time * (1.5 ** attempt), 300)
        print(f"Reserve failed ({result.get('error', '?')}), "
              f"retry {attempt+1}/{max_retries} in {wait:.0f}s")
        time.sleep(wait)
    else:
        raise RuntimeError(f"Failed to reserve machines after {max_retries} attempts")

    try:
        # 5. Deploy artifacts via SCP
        with open(os.path.join(results_path, 'deploy'), 'w', encoding='utf-8') as fp:
            set_log_file(fp)
            deploy_artifacts(config, machine_ips, reservation_id, nic_deploy_info,
                             tree_path, kernel_version)
            set_log_file(None)

        # 6. kexec into new kernel
        kexec_machine(config, machine_ips, reservation_id, mc=mc)

        # 7. Wait for hw-worker with crash monitoring
        has_results = wait_for_results(config, mc, reservation_id,
                                       machine_ids, machine_ips,
                                       results_path=results_path)

        # 8. Copy back results
        if has_results:
            cases = fetch_results(config, machine_ips, reservation_id, rinfo)
        else:
            cases = [{
                'test': 'hw-worker',
                'group': grp_name,
                'result': 'fail',
                'link': link,
            }]
    finally:
        set_log_file(None)
        # 9. Release reservation
        try:
            mc.reservation_close(reservation_id)
        except Exception as e:
            print(f"Warning: failed to close reservation {reservation_id}: {e}")

    print("Done at", datetime.datetime.now())
    return cases


def main():
    """Entry point: set up Fetcher poll loop."""
    cfg_paths = ['hw.config', 'hwksft.config']
    if len(sys.argv) > 1:
        cfg_paths += sys.argv[1:]

    cbarg = CbArg(cfg_paths)
    config = cbarg.config

    base_dir = config.get('local', 'base_path')

    life = NipaLifetime(config)

    f = Fetcher(test, cbarg,
                name=config.get('executor', 'name'),
                branches_url=config.get('remote', 'branches'),
                results_path=os.path.join(base_dir, config.get('local', 'json_path')),
                url_path=config.get('www', 'url') + '/' + config.get('local', 'json_path'),
                tree_path=config.get('local', 'tree_path'),
                patches_path=config.get('local', 'patches_path', fallback=None),
                life=life,
                first_run=config.get('executor', 'init', fallback="continue"))
    f.run()
    life.exit()


if __name__ == '__main__':
    main()
