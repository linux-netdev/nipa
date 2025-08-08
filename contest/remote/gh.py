#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import os
import requests
import subprocess
import sys
import time

from core import NipaLifetime
from lib import Fetcher, CbArg, namify

"""
[executor]
name=
group=
test=
[remote]
branches=
[local]
tree_path=
base_path=
results_path=
json_path=
[www]
url=

[gh]
token=api-token
base=base/branch
link=https://full/link
out_remote=remote-name
out_branch=remote-branch
wait_first=secs-to-first-check
wait_poll=secs-between-rechecks
wait_max=secs-to-wait
[ci]
owner=gh-owner
repo=gh-repo
runs_ref=refs/pull/...
"""

def get(url, token):
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "Authorization": token}
    return requests.get(url, headers=headers)


def link(runid, config):
    return "https://github.com/" + \
           config.get('ci', 'owner') + "/" + \
           config.get('ci', 'repo') + "/" + \
           "actions/runs/" + str(runid)


def gh_namify(name):
    # This may be pretty BPF specific, the test name looks like:
    #    x86_64-gcc / test (test_progs, false, 360) / test_progs on x86_64 with gcc
    name = ' / '.join(name.split(' / ')[:2])
    name = name.replace('test (test', '')
    return namify(name)


def get_jobs_page(config, repo_url, found, token, page=1, res=None):
    resp = get(repo_url + f'/actions/runs/{found["id"]}/jobs?page={page}', token)
    jobs = resp.json()

    if 'jobs' not in jobs:
        print("bad jobs", jobs)
        return None

    if len(jobs['jobs']) == 0:
        if page == 1:
            print("short jobs", jobs)
        return res
    # Must be page 1, init res to empty array
    if res is None:
        res = []

    decoder = {
        'success': 0,
        'skipped': 1,
        None: 2,
        'failure': 3,
        'cancelled': 4,
        'unknown': 5,
    }
    encoder = {
        0: 'pass',
        1: 'pass',
        2: None,
        3: 'fail',
        4: 'fail',
        5: 'fail',
    }

    url = link(found["id"], config)
    for job in jobs["jobs"]:
        if job["conclusion"] is None:
            print("Still running, waiting for job:", job["name"])
            return None
        if job["conclusion"] == 'skipped':
            continue

        if job["conclusion"] in decoder:
            result = encoder[decoder[job["conclusion"]]]
        else:
            print("Unknown result:", job["conclusion"])
            result = 'fail'

        test_link = job.get('html_url', url)

        res.append({'test': gh_namify(job["name"]),
                    'group': config.get('executor', 'group'),
                    'result': result, 'link': test_link})
    if not res:
        print(f"Still waiting, {len(jobs['jobs'])} jobs skipped")
    return get_jobs_page(config, repo_url, found, token, page=(page + 1), res=res)


def get_results(config, cbarg, prev_run, page=1):
    token = config.get('gh', 'token')
    repo_url = f"https://api.github.com/repos/{config.get('ci', 'owner')}/{config.get('ci', 'repo')}"
    ref = config.get('ci', 'runs_ref')

    resp = get(repo_url + f'/actions/runs?page={page}', token)
    runs = resp.json()
    found = None
    for run in runs.get('workflow_runs', []):
        if ref in [r['ref'] for r in run['referenced_workflows']]:
            if found is None or found["id"] < run["id"]:
                found = run
    if found is None:
        if page < 10:
            return get_results(config, cbarg, prev_run, page=(page + 1))
        print(f"Run not found, tried all {page} pages!")
        return None
    if prev_run == found["id"]:
        print("Found old run:", prev_run)
        return None
    cbarg.prev_runid = found["id"]

    return get_jobs_page(config, repo_url, found, token)


def test_run(binfo, rinfo, cbarg, config, start):
    tree_path = config.get('local', 'tree_path')
    base = config.get('gh', 'base')

    subprocess.run('git checkout ' + base, cwd=tree_path, shell=True, check=True)
    res = subprocess.run('git merge ' + rinfo['branch-ref'],
                         cwd=tree_path, shell=True)
    if res.returncode != 0:
        # If rerere fixed it, just commit
        res = subprocess.run('git diff -s --exit-code', cwd=tree_path, shell=True)
        if res.returncode != 0:
            return [{'test': config.get('executor', 'test'),
                     'group': config.get('executor', 'group'),
                     'result': 'skip', 'link': config.get('gh', 'link')}]

        subprocess.run('git commit --no-edit', cwd=tree_path, shell=True, check=True)

    out_remote = config.get('gh', 'out_remote')
    out_branch = config.get('gh', 'out_branch')

    subprocess.run(f'git push -f {out_remote} HEAD:{out_branch}',
                   cwd=tree_path, shell=True, check=True)

    end = start + datetime.timedelta(seconds=config.getint('gh', 'wait_max'))
    time.sleep(config.getint('gh', 'wait_first'))

    prev_runid = 0
    if hasattr(cbarg, "prev_runid"):
        prev_runid = cbarg.prev_runid

    while datetime.datetime.now() < end:
        res = get_results(config, cbarg, prev_runid)
        if res:
            print("Got result:", res)
            return res

        time.sleep(config.getint('gh', 'wait_poll'))

    url = config.get('gh', 'link')
    if hasattr(cbarg, "prev_runid") and cbarg.prev_runid != prev_runid:
        url = link(cbarg.prev_runid, config)

    return [{'test': config.get('executor', 'test'),
             'group': config.get('executor', 'group'),
             'result': 'skip', 'link': url}]


def test(binfo, rinfo, cbarg):
    start = datetime.datetime.now()
    print("Run at", start)

    cbarg.refresh_config()
    config = cbarg.config

    results_path = os.path.join(config.get('local', 'base_path'),
                                config.get('local', 'results_path'),
                                rinfo['run-cookie'])
    os.makedirs(results_path)

    res = test_run(binfo, rinfo, cbarg, config, start)

    retry = []
    for one in res:
        if one['result'] == 'fail':
            retry = test_run(binfo, rinfo, cbarg, config, start)
            break
    for one2 in retry:
        for one in res:
            if one['test'] == one2['test']:
                one['retry'] = one2['result']
                break

    return res


def main() -> None:
    cfg_paths = ['remote.config', 'gh.config']
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
