#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import json
import os
import subprocess

from core import NipaLifetime
from lib import Fetcher, namify


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
    'ERROR': 2,
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
            words = l.strip().split('|')
            if len(words) != 3:
                words = l.split()
            if words[0] not in expected:
                expected[words[0]] = {}
            grp = expected[words[0]]
            if words[1] not in grp:
                grp[words[1]] = {}
            grp[words[1]] = str_to_code[words[2]]
    return expected


def summary_flat(expected, got, sub_path=""):
    if sub_path:
        sub_path += '.'

    overall_code = 0
    results = []
    bad_tests = []
    for case in got["test_cases"]:
        code = str_to_code[case["status"]]

        exp = expected.get(got["name"], {}).get(case["name"])
        if exp and exp == code:
            continue

        name = namify(case["name"])
        overall_code = max(code, overall_code)
        results.append({'test': sub_path + name,
                        'result': code_to_str[code]})
        if code:
            bad_tests.append(f"{got['name']} {name} {case['status']}")

    for sub_group in got["sub_groups"]:
        ov, bt, res = summary_flat(expected, sub_group, sub_path + sub_group["name"])
        overall_code = max(ov, overall_code)
        results += res
        bad_tests += bt

    return overall_code, bad_tests, results


def summary_result(expected, got, link, sub_path=""):
    results = []
    bad_tests = []
    for sub_group in got["sub_groups"]:
        code, bt, res = summary_flat(expected, sub_group)

        data = {
            'test': sub_group["name"],
            'group': 'kunit',
            'result': code_to_str[code],
            'results': res,
            'link': link
        }
        results.append(data)

        bad_tests += bt

    return bad_tests, results


def test(binfo, rinfo, config):
    print("Run at", datetime.datetime.now())

    process = subprocess.Popen(['./tools/testing/kunit/kunit.py', 'run', '--alltests', '--json', '--arch=x86_64'],
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
        results_json = stdout_get_json(stdout)
        if results_json is None:
            raise InfraFail('no JSON')
        expected = load_expected(config)
        bad_tests, cases = summary_result(expected, results_json, link)

        if bad_tests:
            with open(os.path.join(results_path, 'bad_tests'), 'w') as fp:
                fp.write('\n'.join(bad_tests))
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

    life = NipaLifetime(config)

    f = Fetcher(test, config,
                name=config.get('executor', 'name'),
                branches_url=config.get('remote', 'branches'),
                results_path=os.path.join(base_dir, config.get('local', 'json_path')),
                url_path=config.get('www', 'url') + '/' + config.get('local', 'json_path'),
                patches_path=config.get('local', 'patches_path', fallback=None),
                life=life,
                tree_path=config.get('local', 'tree_path'),
                first_run=config.get('executor', 'init', fallback="continue"))
    f.run()
    life.exit()


if __name__ == "__main__":
    main()
