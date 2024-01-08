#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import json
import os
import subprocess

from lib import Fetcher


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


Expected:
group1 test1 skip
group1 test3 fail
group3 testV skip
"""


class InfraFail(Exception):
    pass


str_to_code = {
    'pass': 0,
    'PASS': 0,
    'skip': 1,
    'SKIP': 1,
    'fail': 2,
    'FAIL': 2,
}
code_to_str = {
    0: 'pass',
    1: 'skip',
    2: 'fail',
}

def stdout_get_json(stdout):
    json_start = stdout.find('\n{\n')
    json_end = stdout.find('\n}\n')

    if json_start == -1 or json_end == -1:
        return None
    return json.loads(stdout[json_start:json_end + 2])


def load_expected(config):
    expected = {}
    with open(config.get('local', 'expected', fallback='kunit-expected'), 'r') as fp:
        lines = fp.readlines()
        for l in lines:
            if not l:
                continue
            words = l.split()
            if words[0] not in expected:
                expected[words[0]] = {}
            grp = expected[words[0]]
            if words[1] not in grp:
                grp[words[1]] = {}
            grp[words[1]] = str_to_code[words[2]]
    return  expected


def summary_result(expected, got):
    result = 0
    bad_tests = []
    for sub_group in got["sub_groups"]:
        for case in sub_group["test_cases"]:
            code = str_to_code[case["status"]]

            exp = expected.get(sub_group["name"], {}).get(case["name"])
            if exp and exp == code:
                continue

            result = max(result, code)
            if code:
                bad_tests.append(f"{sub_group['name']} {case['name']} {case['status']}")

    return bad_tests, code_to_str[result]


def test(binfo, rinfo, config):
    print("Run at", datetime.datetime.now())

    process = subprocess.Popen(['./tools/testing/kunit/kunit.py', 'run', '--alltests', '--json'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               cwd=config.get('local', 'tree_path'))
    stdout, stderr = process.communicate()
    stdout = stdout.decode("utf-8", "ignore")
    stderr = stderr.decode("utf-8", "ignore")
    process.stdout.close()
    process.stderr.close()

    results_path = os.path.join(config.get('local', 'base_path'),
                                config.get('local', 'results_path'),
                                rinfo['run-cookie'])
    os.makedirs(results_path)

    link = config.get('www', 'url') + '/' + \
           config.get('local', 'results_path') + '/' + \
           rinfo['run-cookie']

    with open(os.path.join(results_path, 'stdout'), 'w') as fp:
        fp.write(stdout)
    with open(os.path.join(results_path, 'stderr'), 'w') as fp:
        fp.write(stderr)

    try:
        if process.returncode:
            raise InfraFail(f'retcode {process.returncode}')

        results_json = stdout_get_json(stdout)
        if results_json is None:
            raise InfraFail('no JSON')
        expected = load_expected(config)
        bad_tests, res = summary_result(expected, results_json)

        if bad_tests:
            with open(os.path.join(results_path, 'bad_tests'), 'w') as fp:
                fp.write('\n'.join(bad_tests))

        cases = [{'test': config.get('executor', 'test'),
                 'group': config.get('executor', 'group'),
                 'result': res, 'link': link}]
    except InfraFail as e:
        with open(os.path.join(results_path, 'infra_fail'), 'w') as fp:
            fp.write(e.args[0])
        cases = [{'test': config.get('executor', 'test'),
                  'group': config.get('executor', 'group'),
                  'result': 'fail', 'link': link}]

    print("Done at", datetime.datetime.now())

    return cases


def main() -> None:
    config = configparser.ConfigParser()
    config.read(['remote.config'])

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