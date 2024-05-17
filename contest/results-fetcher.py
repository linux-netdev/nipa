#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import couchdb
import datetime
import json
import os
import requests
import time
import uuid


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
[db]
results-name=db-name
branches-name=db-name
user=name
pwd=pass
"""


class FetcherState:
    def __init__(self):
        self.config = configparser.ConfigParser()
        self.config.read(['fetcher.config'])

        # "fetched" is more of a "need state rebuild"
        self.fetched = True

        user = self.config.get("db", "user")
        pwd = self.config.get("db", "pwd")
        server = couchdb.Server(f'http://{user}:{pwd}@127.0.0.1:5984')
        self.res_db = server[self.config.get("db", "results-name", fallback="results")]
        self.brn_db = server[self.config.get("db", "branches-name", fallback="branches")]

    def _one(self, rows):
        rows = list(rows)
        if len(rows) != 1:
            raise Exception("Expected 1 row, found", rows)
        return rows[0]

    def get_branch(self, name):
        branch_info = self.brn_db.find({
            'selector': {
                'branch': name
            }
        })
        return self._one(branch_info)

    def get_wip_row(self, remote, run):
        rows = self.res_db.find({
            'selector': {
                'branch': run["branch"],
                'remote': remote["name"],
                'executor': run["executor"],
                'url': None
            }
        })
        for row in rows:
            return row

    def insert_wip(self, remote, run):
        existing = self.get_wip_row(remote, run)

        branch_info = self.get_branch(run["branch"])

        data = run.copy()
        if existing:
            data['_id'] = existing['_id']
            data['_rev'] = existing['_rev']
        else:
            data['_id'] = uuid.uuid4().hex
        data["remote"] = remote["name"]
        when = datetime.datetime.fromisoformat(branch_info['date'])
        data["start"] = str(when)
        when += datetime.timedelta(hours=2, minutes=58)
        data["end"] = str(when)
        data["results"] = None

        self.res_db.save(data)

    def insert_real(self, remote, run):
        existing = self.get_wip_row(remote, run)

        data = run.copy()
        if existing:
            data['_id'] = existing['_id']
            data['_rev'] = existing['_rev']
        else:
            data['_id'] = uuid.uuid4().hex
        data["remote"] = remote["name"]

        self.res_db.save(data)


def write_json_atomic(path, data):
    tmp = path + '.new'
    with open(tmp, 'w') as fp:
        json.dump(data, fp)
    os.rename(tmp, path)


def fetch_remote_run(fetcher, remote, run_info, remote_state):
    r = requests.get(run_info['url'])
    data = json.loads(r.content.decode('utf-8'))

    fetcher.insert_real(remote, data)

    file = os.path.join(remote_state['dir'], os.path.basename(run_info['url']))
    with open(file, "w") as fp:
        json.dump(data, fp)


def fetch_remote(fetcher, remote, seen):
    print("Fetching remote", remote['url'])
    r = requests.get(remote['url'])
    try:
        manifest = json.loads(r.content.decode('utf-8'))
    except json.decoder.JSONDecodeError:
        print('Failed to decode manifest from remote:', remote['name'])
        return
    remote_state = seen[remote['name']]

    for run in manifest:
        if run['branch'] in remote_state['seen']:
            continue
        if not run['url']:    # Executor has not finished, yet
            if run['branch'] not in remote_state['wip']:
                fetcher.insert_wip(remote, run)
                fetcher.fetched = True
            continue

        print('Fetching run', run['branch'])
        fetch_remote_run(fetcher, remote, run, remote_state)
        fetcher.fetched = True

    with open(os.path.join(remote_state['dir'], 'results.json'), "w") as fp:
        json.dump(manifest, fp)



def build_combined(fetcher, remote_db):
    r = requests.get(fetcher.config.get('input', 'branch_url'))
    branches = json.loads(r.content.decode('utf-8'))
    branch_info = {}
    for br in branches:
        branch_info[br['branch']] = br

    combined = []
    for remote in remote_db:
        name = remote['name']
        dir = os.path.join(fetcher.config.get('output', 'dir'), name)
        print('Combining from remote', name)

        manifest = os.path.join(dir, 'results.json')
        if not os.path.exists(manifest):
            continue

        with open(manifest, "r") as fp:
            results = json.load(fp)

        for entry in results:
            if not entry['url']:    # Executor is running
                if entry['branch'] not in branch_info:
                    continue
                data = entry.copy()
                when = datetime.datetime.fromisoformat(branch_info[entry['branch']]['date'])
                data["start"] = str(when)
                when += datetime.timedelta(hours=2, minutes=58)
                data["end"] = str(when)
                data["results"] = None
            else:
                file = os.path.join(dir, os.path.basename(entry['url']))
                if not os.path.exists(file):
                    print('No file', file)
                    continue
                with open(file, "r") as fp:
                    data = json.load(fp)

            data['remote'] = name
            combined.append(data)
    return combined


def build_seen(fetcher, remote_db):
    seen = {}
    for remote in remote_db:
        seen[remote['name']] = {'seen': set(), 'wip': set()}

        # Prepare local state
        name = remote['name']
        dir = os.path.join(fetcher.config.get('output', 'dir'), name)
        seen[name]['dir'] = dir
        os.makedirs(dir, exist_ok=True)

        url = fetcher.config.get('output', 'url_pfx') + '/' + name
        seen[name]['url'] = url

        # Read the files
        manifest = os.path.join(dir, 'results.json')
        if not os.path.exists(manifest):
            continue

        with open(manifest, "r") as fp:
            results = json.load(fp)
        for entry in results:
            if not entry.get('url'):
                seen[name]['wip'].add(entry.get('branch'))
                print('No URL on', entry, 'from', remote['name'])
                continue
            file = os.path.join(dir, os.path.basename(entry['url']))
            if not os.path.exists(file):
                continue
            seen[name]['seen'].add(entry.get('branch'))
    return seen


def main() -> None:
    fetcher = FetcherState()

    with open(fetcher.config.get('input', 'remote_db'), "r") as fp:
        remote_db = json.load(fp)

    while True:
        if fetcher.fetched:
            seen = build_seen(fetcher, remote_db)
            fetcher.fetched = False

        for remote in remote_db:
            fetch_remote(fetcher, remote, seen)

        if fetcher.fetched:
            print('Generating combined')
            results = build_combined(fetcher, remote_db)

            combined = os.path.join(fetcher.config.get('output', 'dir'),
                                    fetcher.config.get('output', 'combined'))
            write_json_atomic(combined, results)

        time.sleep(int(fetcher.config.get('cfg', 'refresh')))


if __name__ == "__main__":
    main()
