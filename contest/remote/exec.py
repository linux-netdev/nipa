#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import os
import subprocess

from core import NipaLifetime
from lib import Fetcher


"""
Config:

[executor]
name=executor
group=test-group
test=test-name
[bin]
exec=./script.sh
[remote]
branches=https://url-to-branches-manifest
[local]
base_path=/common/path
json_path=base-relative/path/to/json
results_path=base-relative/path/to/raw/outputs
tree_path=/root-path/to/kernel/git
[www]
url=https://url-to-reach-base-path
"""


def test(binfo, rinfo, config):
    print("Run at", datetime.datetime.now())

    results_path = os.path.join(config.get('local', 'base_path'),
                                config.get('local', 'results_path'),
                                rinfo['run-cookie'])
    os.makedirs(results_path)

    env = os.environ.copy()
    env['BRANCH'] = binfo['branch']
    env['BASE'] = binfo['base']
    env['RESULTS_DIR'] = results_path

    bin = config.get('bin', 'exec').split()
    process = subprocess.Popen(bin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=env, cwd=config.get('local', 'tree_path'))
    stdout, stderr = process.communicate()
    stdout = stdout.decode("utf-8", "ignore")
    stderr = stderr.decode("utf-8", "ignore")
    process.stdout.close()
    process.stderr.close()

    with open(os.path.join(results_path, 'stdout'), 'w') as fp:
        fp.write(stdout)
    with open(os.path.join(results_path, 'stderr'), 'w') as fp:
        fp.write(stderr)

    if process.returncode == 0:
        res = 'pass'
    elif process.returncode == 4:
        res = 'skip'
    elif process.returncode == 5:
        res = 'warn'
    else:
        res = 'fail'

    link = config.get('www', 'url') + '/' + \
           config.get('local', 'results_path') + '/' + \
           rinfo['run-cookie']

    return [{'test': config.get('executor', 'test'),
             'group': config.get('executor', 'group'),
             'result': res, 'link': link}]


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
                tree_path=config.get('local', 'tree_path'),
                life=life,
                first_run=config.get('executor', 'init', fallback="continue"))
    f.run()
    life.exit()


if __name__ == "__main__":
    main()