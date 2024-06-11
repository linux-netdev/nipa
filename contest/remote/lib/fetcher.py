# SPDX-License-Identifier: GPL-2.0

import datetime
import json
import os
import re
import requests
import subprocess
import time


class Fetcher:
    def __init__(self, cb, cbarg, name, branches_url, results_path, url_path, tree_path,
                 patches_path, life, first_run="continue"):
        self._cb = cb
        self._cbarg = cbarg
        self.name = name
        self.life = life

        self._branches_url = branches_url

        self._results_path = results_path
        self._url_path = url_path
        self._results_manifest = os.path.join(results_path, 'results.json')

        self._tree_path = tree_path
        self._patches_path = patches_path

        # Set last date to something old
        self._last_date = datetime.datetime.now(datetime.UTC) - datetime.timedelta(weeks=1)
        if first_run == "force":
            # leave _last_date very old, this will force run on newest branch
            pass
        elif first_run == "continue":
            try:
                r = requests.get(self._branches_url)
                branches = json.loads(r.content.decode('utf-8'))
                branch_date = {}
                for b in branches:
                    branch_date[b["branch"]] = datetime.datetime.fromisoformat(b["date"])

                with open(self._results_manifest, "rb") as fp:
                    old_db = json.load(fp)
                for result in old_db:
                    if 'url' not in result or not result['url']:
                        continue
                    if result["branch"] not in branch_date:
                        continue

                    self._last_date = max(branch_date[result["branch"]], self._last_date)
                print("INFO: Last run date:", self._last_date)
            except FileNotFoundError:
                pass
        elif first_run == "next":
            # unless there's a crazy race or time error this will skip newest branch
            self._last_date = datetime.datetime.now(datetime.UTC)

    def _result_set(self, branch_name, url):
        try:
            with open(self._results_manifest, "rb") as fp:
                old_db = json.load(fp)
        except FileNotFoundError:
            old_db = []

        found = False
        for entry in old_db:
            if entry['branch'] == branch_name:
                entry["url"] = url
                found = True
                break
        if not found:
            old_db.append({'url': None, 'branch': branch_name, 'executor': self.name})

        with open(self._results_manifest, "w") as fp:
            json.dump(old_db, fp)

    def _write_result(self, data, run_cookie):
        file_name = f'results-{run_cookie}.json'

        with open(os.path.join(self._results_path, file_name), "w") as fp:
            json.dump(data, fp)

        return self._url_path + '/' + file_name

    def _run_test(self, binfo):
        self._result_set(binfo['branch'], None)

        start = datetime.datetime.now(datetime.UTC)
        run_id_cookie = str(int(start.timestamp() / 60) % 1000000)
        rinfo = {'run-cookie': run_id_cookie}
        results = self._cb(binfo, rinfo, self._cbarg)
        end = datetime.datetime.now(datetime.UTC)

        entry = {
            'executor': self.name,
            'branch': binfo['branch'],
            'start': str(start),
            'end': str(end),
            'results': results,
        }
        if 'link' in rinfo:
            entry['link'] = rinfo['link']
        url = self._write_result(entry, run_id_cookie)

        self._result_set(binfo['branch'], url)

    def _clean_old_branches(self, remote, current):
        ret = subprocess.run('git branch', shell=True, capture_output=True)
        existing = set([x.strip() for x in ret.stdout.decode('utf-8').split('\n')])

        for b in remote:
            if b["branch"] in existing and b["branch"] != current:
                subprocess.run('git branch -d ' + b["branch"],
                               cwd=self._tree_path, shell=True)

    def _run_once(self):
        r = requests.get(self._branches_url)
        branches = json.loads(r.content.decode('utf-8'))

        to_test = None
        newest = self._last_date

        for b in branches:
            when = datetime.datetime.fromisoformat(b["date"])
            if when > newest:
                newest = when
                to_test = b

        if not to_test:
            print("Nothing to test, prev:", self._last_date)
            return

        print("Testing ", to_test)
        self._last_date = newest

        if self._patches_path is not None:
            subprocess.run('git restore .', cwd=self._tree_path,
                           shell=True)

        # For now assume URL is in one of the remotes
        subprocess.run('git fetch --all --prune', cwd=self._tree_path,
                       shell=True)
        subprocess.run('git checkout ' + to_test["branch"],
                       cwd=self._tree_path, shell=True, check=True)

        if self._patches_path is not None:
            for patch in sorted(os.listdir(self._patches_path)):
                realpath = '{}/{}'.format(self._patches_path, patch)
                subprocess.run('git apply -v {}'.format(realpath),
                               cwd=self._tree_path, shell=True)

        self._clean_old_branches(branches, to_test["branch"])
        self._run_test(to_test)

    def run(self):
        while self.life.next_poll():
            self._run_once()


def namify(what):
    name = re.sub(r'[^0-9a-zA-Z]+', '-', what)
    if name[-1] == '-':
        name = name[:-1]
    return name
