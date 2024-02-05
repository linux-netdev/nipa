#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import datetime
import shutil
import fcntl
import sys
import os

from core import NipaLifetime
from lib import CbArg
from lib import Fetcher
from lib import VM, new_vm, guess_indicators


"""
Config:

[executor]
name=executor
group=test-group
test=test-name
init=force / continue / next
[remote]
branches=https://url-to-branches-manifest
[local]
base_path=/common/path
json_path=base-relative/path/to/json
results_path=base-relative/path/to/raw/outputs
tree_path=/root-path/to/kernel/git
patches_path=/root-path/to/patches/dir
[www]
url=https://url-to-reach-base-path
# Specific stuff
[env]
paths=/extra/exec/PATH:/another/bin
[vm]
paths=/extra/exec/PATH:/another/bin
init_prompt=expected_on-boot#
virtme_opt=--opt,--another one
default_timeout=15
boot_timeout=45


Expected:
group1 test1 skip
group1 test3 fail
group3 testV skip
"""


def test(binfo, rinfo, cbarg):
    print("Run at", datetime.datetime.now())
    cbarg.refresh_config()
    config = cbarg.config

    results_path = os.path.join(config.get('local', 'base_path'),
                                config.get('local', 'results_path'),
                                rinfo['run-cookie'])
    os.makedirs(results_path)

    link = config.get('www', 'url') + '/' + \
           config.get('local', 'results_path') + '/' + \
           rinfo['run-cookie']
    rinfo['link'] = link

    cases = []

    vm = VM(config)
    vm.build(["tools/testing/selftests/drivers/net/netdevsim/config"])
    shutil.copy(os.path.join(config.get('local', 'tree_path'), '.config'),
                results_path + '/config')
    vm.dump_log(results_path + '/build')

    vm_id = 0
    vm_id, vm = new_vm(results_path, vm_id, vm=vm, cwd="tools/testing/selftests/drivers/net/netdevsim/")

    dir_path = config.get('local', 'tree_path') + "/tools/testing/selftests/drivers/net/netdevsim"
    for test in os.listdir(dir_path):
        file_path = os.path.join(dir_path, test)
        if not os.path.isfile(file_path) or not os.access(file_path, os.X_OK):
            print("< skip " + test)
            continue

        print(f"INFO: running test ===", test)
        vm.cmd("./" + test)

        try:
            vm.drain_to_prompt()
            if vm.fail_state:
                retcode = 1
            else:
                retcode = vm.bash_prev_retcode()
        except TimeoutError:
            vm.ctrl_c()
            vm.drain_to_prompt()
            retcode = 1

        indicators = guess_indicators(vm.log_out)

        result = 'pass'
        if indicators["skip"] or not indicators["pass"]:
            result = 'skip'

        if retcode == 4:
            result = 'skip'
        elif retcode:
            result = 'fail'
        if indicators["fail"]:
            result = 'fail'

        if vm.fail_state == 'oops':
            vm.extract_crash(results_path + '/vm-crash-' + str(vm_id))
        vm.dump_log(results_path + '/' + test, result=retcode,
                    info={"vm-id": vm_id, "found": indicators, "vm_state": vm.fail_state})

        print("> retcode:", retcode, "result:", result, "found", indicators)

        cases.append({'test': test, 'group': 'netdevsim', 'result': result,
                      'link': link + '/' + test})

        if vm.fail_state:
            print("INFO: VM kernel crashed, starting a clean one!")
            vm.stop()
            vm.dump_log(results_path + '/vm-stop-' + str(vm_id))
            vm_id, vm = new_vm(results_path, vm_id, config=config,
                               cwd="tools/testing/selftests/drivers/net/netdevsim/")

    vm.stop()
    vm.dump_log(results_path + '/vm-stop-' + str(vm_id))

    print("Done at", datetime.datetime.now())

    return cases


def main() -> None:
    cfg_paths = ['remote.config']
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
                patches_path=config.get('local', 'patches_path'),
                life=life,
                first_run=config.get('executor', 'init', fallback="continue"))
    f.run()
    life.exit()


if __name__ == "__main__":
    main()
