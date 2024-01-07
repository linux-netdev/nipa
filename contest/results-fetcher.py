#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import json
import os
import requests
import time


"""
Config:

[cfg]
refresh=#secs
[input]
remote_db=/path/to/db
[output]
dir=/path/to/output
url_pfx=relative/within/server
combined=name-of-manifest.json
"""

def fetch_remote_run(run_info, remote_state):
    r = requests.get(run_info['url'])
    data = json.loads(r.content.decode('utf-8'))

    file = os.path.join(remote_state['dir'], os.path.basename(run_info['url']))
    with open(file, "w") as fp:
        json.dump(data, fp)


def fetch_remote(remote, seen):
    print("Fetching remote", remote['url'])
    r = requests.get(remote['url'])
    manifest = json.loads(r.content.decode('utf-8'))
    remote_state = seen[remote['name']]

    fetched = False
    for run in manifest:
        if run['branch'] in remote_state['seen']:
            print('Skip:', remote['name'], '-', run['branch'], '- already fetched')
            continue
        if not run['url']:    # Executor has not finished, yet
            continue

        print('Fetching run', run['branch'])
        fetch_remote_run(run, remote_state)
        fetched = True

    with open(os.path.join(remote_state['dir'], 'results.json'), "w") as fp:
        json.dump(manifest, fp)

    return fetched


def build_combined(config, remote_db):
    combined = []
    for remote in remote_db:
        name = remote['name']
        dir = os.path.join(config.get('output', 'dir'), name)
        print('Combining from remote', name)

        manifest = os.path.join(dir, 'results.json')
        if not os.path.exists(manifest):
            continue

        with open(manifest, "r") as fp:
            results = json.load(fp)

        for entry in results:
            if not entry['url']:    # Executor is running
                continue

            file = os.path.join(dir, os.path.basename(entry['url']))
            if not os.path.exists(file):
                print('No file', file)
                continue
            with open(file, "r") as fp:
                data = json.load(fp)

            for test in data['results']:
                row = {}
                for k in ['executor', 'branch', 'start', 'end']:
                    row[k] = data[k]
                row['remote'] = name
                row.update(test)
                combined.append(row)
    return combined


def build_seen(config, remote_db):
    seen = {}
    for remote in remote_db:
        seen[remote['name']] = {'seen': set()}

        # Prepare local state
        name = remote['name']
        dir = os.path.join(config.get('output', 'dir'), name)
        seen[name]['dir'] = dir
        os.makedirs(dir, exist_ok=True)

        url = config.get('output', 'url_pfx') + '/' + name
        seen[name]['url'] = url

        # Read the files
        manifest = os.path.join(dir, 'results.json')
        if not os.path.exists(manifest):
            continue

        with open(manifest, "r") as fp:
            results = json.load(fp)
        for entry in results:
            if not entry.get('url'):
                print('No URL on', entry, 'from', remote['name'])
                continue
            file = os.path.join(dir, os.path.basename(entry['url']))
            if not os.path.exists(file):
                continue
            seen[name]['seen'].add(entry.get('branch'))
    return seen


def one_check(config, remote_db, seen):
    fetched = False
    for remote in remote_db:
        fetched |= fetch_remote(remote, seen)
    return fetched


def main() -> None:
    config = configparser.ConfigParser()
    config.read(['fetcher.config'])

    with open(config.get('input', 'remote_db'), "r") as fp:
        remote_db = json.load(fp)

    fetched = True
    while True:
        if fetched:
            seen = build_seen(config, remote_db)

        fetched = one_check(config, remote_db, seen)

        if fetched:
            print('Generating combined')
            results = build_combined(config, remote_db)

            combined = os.path.join(config.get('output', 'dir'),
                                    config.get('output', 'combined'))
            with open(combined, "w") as fp:
                json.dump(results, fp)

        time.sleep(int(config.get('cfg', 'refresh')))


if __name__ == "__main__":
    main()
