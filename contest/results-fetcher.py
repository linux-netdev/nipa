#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import copy
import datetime
import json
import os
import psycopg2
import requests
import time
import traceback
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
db=db-name
results-name=table-name
branches-name=table-name
"""


class FetcherState:
    def __init__(self):
        self.config = configparser.ConfigParser()
        self.config.read(['fetcher.config'])

        # "fetched" is more of a "need state rebuild"
        self.fetched = True

        self.tbl_res = self.config.get("db", "results-name", fallback="results")
        self.tbl_brn = self.config.get("db", "branches-name", fallback="branches")

        db_name = self.config.get("db", "db")
        self.psql_conn = psycopg2.connect(database=db_name)
        self.psql_conn.autocommit = True

    def get_branch(self, name):
        with self.psql_conn.cursor() as cur:
            cur.execute(f"SELECT info FROM {self.tbl_brn} WHERE branch = '{name}'")
            rows = cur.fetchall()
        return json.loads(rows[0][0])

    def psql_run_selector(self, cur, remote, run):
        return cur.mogrify("WHERE branch = %s AND remote = %s AND executor = %s",
                           (run['branch'], remote["name"], run["executor"],)).decode('utf-8')

    def psql_has_wip(self, remote, run):
        with self.psql_conn.cursor() as cur:
            cur.execute(f"SELECT branch FROM {self.tbl_res} " + self.psql_run_selector(cur, remote, run))
            rows = cur.fetchall()
        return rows and len(rows) > 0

    def insert_result_psql(self, cur, data):
        normal, full = self.psql_json_split(data)
        arg = cur.mogrify("(%s,%s,%s,%s,%s,%s,%s)", (data["branch"], data["remote"], data["executor"],
                                                     data["start"], data["end"], normal, full))
        cur.execute(f"INSERT INTO {self.tbl_res} VALUES " + arg.decode('utf-8'))

    def insert_wip(self, remote, run):
        if self.psql_has_wip(remote, run):
            # no point, we have no interesting info to add
            return

        branch_info = self.get_branch(run["branch"])

        data = run.copy()
        data["remote"] = remote["name"]
        when = datetime.datetime.fromisoformat(branch_info['date'])
        data["start"] = str(when)
        when += datetime.timedelta(hours=2, minutes=58)
        data["end"] = str(when)
        data["results"] = None

        with self.psql_conn.cursor() as cur:
            self.insert_result_psql(cur, data)

    def psql_json_split(self, data):
        # return "normal" and "full" as json string or None
        # "full" will be None if they are the same to save storage
        if data.get("results") is None:
            return json.dumps(data), None

        normal = copy.deepcopy(data)
        full = None

        for row in normal["results"]:
            if "results" in row:
                full = True
                del row["results"]

        if full:
            full = json.dumps(data)
        return json.dumps(normal), full

    def insert_real(self, remote, run):
        data = run.copy()
        data["remote"] = remote["name"]

        with self.psql_conn.cursor() as cur:
            if not self.psql_has_wip(remote, run):
                self.insert_result_psql(cur, data)
            else:
                normal, full = self.psql_json_split(data)
                vals = cur.mogrify("SET t_start = %s, t_end = %s, json_normal = %s, json_full = %s",
                                   (data["start"], data["end"], normal, full)).decode('utf-8')
                selector = self.psql_run_selector(cur, remote, run)
                q = f"UPDATE {self.tbl_res} " + vals + ' ' + selector
                cur.execute(q)


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
