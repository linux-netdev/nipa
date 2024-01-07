# SPDX-License-Identifier: GPL-2.0

import datetime
import json
import os
import requests
import subprocess
import time


class Fetcher:
    def __init__(self, cb, cbarg, name, branches_url, results_path, url_path, tree_path, check_sec=60):
        self._cb = cb
        self._cbarg = cbarg
        self.name = name

        self._branches_url = branches_url
        self._check_secs = check_sec

        self._results_path = results_path
        self._url_path = url_path
        self._results_manifest = os.path.join(results_path, 'results.json')

        self._tree_path = tree_path

        # Set last date to something old
        self._last_date = datetime.datetime.now(datetime.UTC) - datetime.timedelta(weeks=1)

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
        results = self._cb(binfo, {'run-cookie': run_id_cookie}, self._cbarg)
        end = datetime.datetime.now(datetime.UTC)

        entry = {
            'executor': self.name,
            'branch': binfo['branch'],
            'start': str(start),
            'end': str(end),
            'results': results,
        }
        url = self._write_result(entry, run_id_cookie)

        self._result_set(binfo['branch'], url)

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
        # For now assume URL is in one of the remotes
        subprocess.run('git fetch --all', cwd=self._tree_path, shell=True)
        subprocess.run('git checkout ' + to_test["branch"],
                       cwd=self._tree_path, shell=True, check=True)
        self._run_test(to_test)

    def run(self):
        while True:
            self._run_once()
            try:
                time.sleep(self._check_secs)
            except KeyboardInterrupt:
                return
