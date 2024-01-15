#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import shutil
import fcntl
import os
import re
import sys

from lib import Fetcher
from lib import VM, new_vm, guess_indicators


"""
Config:

[executor]
name=executor
group=test-group
test=test-name
[remote]
branches=https://url-to-branches-manifest
[local]
base_path=/common/path
json_path=base-relative/path/to/json
results_path=base-relative/path/to/raw/outputs
tree_path=/root-path/to/kernel/git
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
[ksft]
targets=net


Expected:
group1 test1 skip
group1 test3 fail
group3 testV skip
"""


def ktap_split(full_run):
    tests = []
    test = None
    test_id = 0

    result_re = re.compile(r"(not )?ok (\d+) ([^#]*[^ ])( # )?([^ ].*)?$")

    for line in full_run.split('\n'):
        if test is None:
            test = {
                "tid": test_id,
                "sid": None,
                "output": [],
                "name": None,
                "result": None,
                "comment": None,
            }
            test_id += 1

        test["output"].append(line)
        if line.startswith("ok "):
            test["result"] = "pass"
        elif line.startswith("not ok "):
            test["result"] = "fail"

        if not test["result"]:
            continue

        v = result_re.match(line).groups()
        test["output"] = "\n".join(test["output"])
        test["sid"] = int(v[1])
        test["name"] = v[2]
        if len(v) > 4:
            test["comment"] = v[4]
            if v[4] == "SKIP" and test["result"] == "pass":
                test["result"] = "skip"
        tests.append(test)
        test = None

    return tests


def ktap_extract_pfx(tests):
    t_names = [t["name"] for t in tests]
    if len(t_names) == 1:
        # If there's only one test the whole thing is "common"
        idx = t_names[0].rfind(':')
        pfx = t_names[0][:idx + 2]
    else:
        pfx = os.path.commonprefix(t_names)
        if not pfx:
            raise Exception("No common prefix found", t_names)

    for test in tests:
        test["name"] = test["name"][len(pfx):]

    return pfx.strip()


def namify(what):
    name = re.sub(r'[^0-9a-zA-Z]+', '-', what)
    if name[-1] == '-':
        name = name[:-1]
    return name


def test(binfo, rinfo, config):
    print("Run at", datetime.datetime.now())

    results_path = os.path.join(config.get('local', 'base_path'),
                                config.get('local', 'results_path'),
                                rinfo['run-cookie'])
    os.makedirs(results_path)

    link = config.get('www', 'url') + '/' + \
           config.get('local', 'results_path') + '/' + \
           rinfo['run-cookie']
    rinfo['link'] = link
    target = config.get('ksft', 'target')

    cases = []

    vm = VM(config)
    vm.build([f"tools/testing/selftests/{target}/config"])
    shutil.copy(os.path.join(config.get('local', 'tree_path'), '.config'),
                results_path + '/config')
    vm.tree_cmd("make headers")
    vm.tree_cmd(f"make -C tools/testing/selftests/{target}/")
    vm.dump_log(results_path + '/build')

    _, vm = new_vm(results_path, 0, vm=vm)

    print(f"INFO: starting test")
    vm.cmd(f"make -C tools/testing/selftests TARGETS={target} run_tests")

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

    if vm.fail_state == 'oops':
        vm.extract_crash(results_path + '/vm-crash')

    full_run = vm.log_out
    vm.dump_log(results_path + '/full', result=retcode, info={"vm_state": vm.fail_state})

    tests = ktap_split(full_run)
    if tests:
        pfx = ktap_extract_pfx(tests)
        grp_name = namify(pfx)
    else:
        cases = [{'test': 'infra', 'group': 'all',
                  'result': 'fail', 'link': link}]

    for test in tests:
        indicators = guess_indicators(test["output"])

        if test["result"] == "pass" and indicators["skip"]:
            test["result"] = 'skip'
            print("INFO: scan override SKIP")
        if test["result"] != "fail" and indicators["fail"]:
            test["result"] = 'fail'
            print("INFO: scan override FAIL")

        test_name = namify(test["name"])
        fname = str(test["sid"]) + "-" + test_name
        with open(os.path.join(results_path, fname), 'w') as fp:
            fp.write(test["output"])


        print("> reported:", test["name"], "result:", test["result"])

        cases.append({'test': test_name, 'group': grp_name, 'result': test["result"],
                      'link': link + '/' + fname})

    with open(os.path.join(results_path, "full", "info"), 'w') as fp:
        fp.write(repr(tests))

    vm.stop()
    vm.dump_log(results_path + '/vm-stop')

    print("Done at", datetime.datetime.now())

    return cases


def main() -> None:
    config = configparser.ConfigParser()
    config.read(['remote.config', 'vmksft.config'])
    if len(sys.argv) > 1:
        config.read(sys.argv[1:])

    base_dir = config.get('local', 'base_path')

    f = Fetcher(test, config,
                name=config.get('executor', 'name'),
                branches_url=config.get('remote', 'branches'),
                results_path=os.path.join(base_dir, config.get('local', 'json_path')),
                url_path=config.get('www', 'url') + '/' + config.get('local', 'json_path'),
                tree_path=config.get('local', 'tree_path'))
    f.run()


if __name__ == "__main__":
    main()
