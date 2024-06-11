#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import datetime
import shutil
import fcntl
import os
import re
import queue
import sys
import tempfile
import threading
import time

from core import NipaLifetime
from lib import wait_loadavg
from lib import CbArg
from lib import Fetcher, namify
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
[ksft]
targets=net
nested_tests=off / on


Expected:
group1 test1 skip
group1 test3 fail
group3 testV skip
"""


def get_prog_list(vm, target):
    tmpdir = tempfile.mkdtemp()
    vm.tree_cmd(f"make -C tools/testing/selftests/ TARGETS={target} INSTALL_PATH={tmpdir} install")

    with open(os.path.join(tmpdir, 'kselftest-list.txt'), "r") as fp:
        targets = fp.readlines()
    vm.tree_cmd("rm -rf " + tmpdir)
    return [e.split(":")[1].strip() for e in targets]


def _parse_nested_tests(full_run):
    tests = []
    nested_tests = False

    result_re = re.compile(r"(not )?ok (\d+)( -)? ([^#]*[^ ])( # )?([^ ].*)?$")

    for line in full_run.split('\n'):
        # nested subtests support: we parse the comments from 'TAP version'
        if nested_tests:
            if line.startswith("# "):
                line = line[2:]
            else:
                nested_tests = False
        elif line.startswith("# TAP version "):
            nested_tests = True
            continue

        if not nested_tests:
            continue

        if line.startswith("ok "):
            result = "pass"
        elif line.startswith("not ok "):
            result = "fail"
        else:
            continue

        v = result_re.match(line).groups()
        name = v[3]
        if len(v) > 5 and v[5]:
            if v[5].lower().startswith('skip') and result == "pass":
                result = "skip"
        tests.append((name, result))

    return tests

def _vm_thread(config, results_path, thr_id, hard_stop, in_queue, out_queue):
    target = config.get('ksft', 'target')
    vm = None
    vm_id = -1

    while True:
        try:
            work_item = in_queue.get(block=False)
        except queue.Empty:
            print(f"INFO: thr-{thr_id} has no more work, exiting")
            break

        test_id = work_item['tid']
        prog = work_item['prog']
        test_name = namify(prog)
        file_name = f"{test_id}-{test_name}"
        is_retry = 'result' in work_item

        deadline = (hard_stop - datetime.datetime.now(datetime.UTC)).total_seconds()

        if is_retry:
            file_name += '-retry'
        # Don't run retries if we can't finish with 10min to spare
        if is_retry and deadline - work_item['time'] < 10 * 60:
            print(f"INFO: thr-{thr_id} retry skipped == " + prog)
            out_queue.put(work_item)
            continue

        if vm is None:
            vm_id, vm = new_vm(results_path, vm_id, config=config, thr=thr_id)

        print(f"INFO: thr-{thr_id} testing == " + prog)
        t1 = datetime.datetime.now()
        vm.cmd(f'make -C tools/testing/selftests TARGETS={target} TEST_PROGS={prog} TEST_GEN_PROGS="" run_tests')
        try:
            vm.drain_to_prompt(deadline=deadline)
            retcode = vm.bash_prev_retcode()
        except TimeoutError:
            print(f"INFO: thr-{thr_id} test timed out:", prog)
            vm.kill_current_cmd()
            retcode = 1

        t2 = datetime.datetime.now()

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

        crashes = None
        if vm.fail_state == 'oops':
            print(f"INFO: thr-{thr_id} test crashed kernel:", prog)
            crashes = vm.extract_crash(results_path + f'/vm-crash-thr{thr_id}-{vm_id}')
            # Extraction will clear/discard false-positives (ignored traces)
            # check VM is still in failed state
            if vm.fail_state:
                result = "fail"

        print(f"INFO: thr-{thr_id} {prog} >> retcode:", retcode, "result:", result, "found", indicators)

        if is_retry:
            outcome = work_item
            outcome['retry'] = result
        else:
            outcome = {'tid': test_id, 'prog': prog, 'test': test_name, 'file_name': file_name,
                       'result': result, 'time': (t2 - t1).total_seconds()}
            if crashes:
                outcome['crashes'] = crashes

        if not is_retry and result == 'fail':
            in_queue.put(outcome)
        else:
            out_queue.put(outcome)

        if config.getboolean('ksft', 'nested_tests', fallback=False) and not is_retry:
            # this will only parse nested tests inside the TAP comments
            tests = _parse_nested_tests(vm.log_out)

            for r_name, r_result in tests:
                out_queue.put({'prog': prog, 'test': namify(r_name),
                               'file_name': file_name, 'result': r_result})

            print(f"INFO: thr-{thr_id} {prog} >> nested tests: {len(tests)} subtests")

        vm.dump_log(results_path + '/' + file_name, result=retcode,
                    info={"thr-id": thr_id, "vm-id": vm_id, "time": (t2 - t1).total_seconds(),
                          "found": indicators, "vm_state": vm.fail_state})

        if vm.fail_state:
            print(f"INFO: thr-{thr_id} VM kernel crashed, destroying it")
            vm.stop()
            vm.dump_log(results_path + f'/vm-stop-thr{thr_id}-{vm_id}')
            vm = None

    if vm is not None:
        vm.stop()
        vm.dump_log(results_path + f'/vm-stop-thr{thr_id}-{vm_id}')
    return


def vm_thread(config, results_path, thr_id, hard_stop, in_queue, out_queue):
    try:
        _vm_thread(config, results_path, thr_id, hard_stop, in_queue, out_queue)
    except Exception:
        print(f"ERROR: thr-{thr_id} has crashed")
        raise


def test(binfo, rinfo, cbarg):
    print("Run at", datetime.datetime.now())
    if not hasattr(cbarg, "prev_runtime"):
        cbarg.prev_runtime = dict()
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
    target = config.get('ksft', 'target')
    grp_name = "selftests-" + namify(target)

    vm = VM(config)

    if vm.build([f"tools/testing/selftests/{target}/config"]) == False:
        vm.dump_log(results_path + '/build')
        return [{
            'test': 'build',
            'group': grp_name,
            'result': 'fail',
            'link': link + '/build',
        }]

    shutil.copy(os.path.join(config.get('local', 'tree_path'), '.config'),
                results_path + '/config')
    vm.tree_cmd("make headers")
    vm.tree_cmd(f"make -C tools/testing/selftests/{target}/")
    vm.dump_log(results_path + '/build')

    progs = get_prog_list(vm, target)
    progs.sort(reverse=True, key=lambda prog : cbarg.prev_runtime.get(prog, 0))

    dl_min = config.getint('executor', 'deadline_minutes', fallback=999999)
    hard_stop = datetime.datetime.fromisoformat(binfo["date"])
    hard_stop += datetime.timedelta(minutes=dl_min)

    in_queue = queue.Queue()
    out_queue = queue.Queue()
    threads = []

    i = 0
    for prog in progs:
        i += 1
        in_queue.put({'tid': i, 'prog': prog})

    # In case we have multiple tests kicking off on the same machine,
    # add optional wait to make sure others have finished building
    load_tgt = config.getfloat("cfg", "wait_loadavg", fallback=None)
    thr_cnt = int(config.get("cfg", "thread_cnt"))
    delay = float(config.get("cfg", "thread_spawn_delay", fallback=0))
    for i in range(thr_cnt):
        wait_loadavg(load_tgt)
        print("INFO: starting VM", i)
        threads.append(threading.Thread(target=vm_thread,
                                        args=[config, results_path, i, hard_stop,
                                              in_queue, out_queue]))
        threads[i].start()
        time.sleep(delay)

    for i in range(thr_cnt):
        threads[i].join()

    cases = []
    while not out_queue.empty():
        r = out_queue.get()
        if 'time' in r:
            cbarg.prev_runtime[r["prog"]] = r["time"]
        outcome = {
            'test': r['test'],
            'group': grp_name,
            'result': r["result"],
            'link': link + '/' + r['file_name']
        }
        for key in ['retry', 'crashes']:
            if key in r:
                outcome[key] = r[key]
        cases.append(outcome)
    if not in_queue.empty():
        print("ERROR: in queue is not empty")

    print("Done at", datetime.datetime.now())

    return cases


def main() -> None:
    cfg_paths = ['remote.config', 'vmksft.config', 'vmksft-p.config']
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


if __name__ == "__main__":
    main()
