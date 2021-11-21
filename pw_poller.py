#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.
# Copyright (c) 2020 Facebook

import configparser
import datetime
import json
import os
import threading
import shutil
import time
import queue
from typing import Dict

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from core import Tester
from core import Tree
from pw import Patchwork
from pw import PwSeries
import core
import netdev


class IncompleteSeries(Exception):
    pass


class PwPoller:
    def __init__(self) -> None:
        config = configparser.ConfigParser()
        config.read(['nipa.config', 'pw.config', 'poller.config'])

        log_init(config.get('log', 'type', fallback='org'),
                 config.get('log', 'file', fallback=os.path.join(NIPA_DIR, "poller.org")))

        self._worker_id = 0
        self._async_workers = []

        self.result_dir = config.get('dirs', 'results', fallback=os.path.join(NIPA_DIR, "results"))
        self.worker_dir = config.get('dirs', 'workers', fallback=os.path.join(NIPA_DIR, "workers"))
        tree_dir = config.get('dirs', 'trees', fallback=os.path.join(NIPA_DIR, "../"))
        self._trees = { }
        for tree in config['trees']:
            opts = [x.strip() for x in config['trees'][tree].split(',')]
            prefix = opts[0]
            fspath = opts[1]
            remote = opts[2]
            branch = None
            if len(opts) > 3:
                branch = opts[3]
            src = os.path.join(tree_dir, fspath)
            # name, pfx, fspath, remote=None, branch=None
            self._trees[tree] = Tree(tree, prefix, src, remote=remote, branch=branch)

        if os.path.exists(self.worker_dir):
            shutil.rmtree(self.worker_dir)
        os.makedirs(self.worker_dir)

        self._barrier = threading.Barrier(len(self._trees) + 1)
        self._done_queue = queue.Queue()
        self._workers = {}
        for k, tree in self._trees.items():
            self._workers[k] = Tester(self.result_dir, tree, queue.Queue(), self._done_queue,
                                      self._barrier)
            self._workers[k].start()
            log(f"Started worker {self._workers[k].name} for {k}")

        self._pw = Patchwork(config)

        self._state = {
            'last_poll': (datetime.datetime.utcnow() - datetime.timedelta(hours=2)).timestamp(),
            'done_series': [],
        }
        self.init_state_from_disk()
        self.seen_series = set(self._state['done_series'])
        self.done_series = self.seen_series.copy()

    def init_state_from_disk(self) -> None:
        try:
            with open('poller.state', 'r') as f:
                loaded = json.load(f)

                for k in loaded.keys():
                    self._state[k] = loaded[k]
        except FileNotFoundError:
            pass

    def _series_determine_tree(self, s: PwSeries) -> str:
        s.tree_name = netdev.series_tree_name_direct(s)
        s.tree_mark_expected = True
        s.tree_marked = bool(s.tree_name)

        if s.is_pure_pull():
            if s.title.find('-next') >= 0:
                s.tree_name = 'net-next'
            else:
                s.tree_name = 'net'
            s.tree_mark_expected = None
            return f"Pull request for {s.tree_name}"

        if s.tree_name:
            log(f'Series is clearly designated for: {s.tree_name}', "")
            return f"Clearly marked for {s.tree_name}"

        s.tree_mark_expected, should_test = netdev.series_tree_name_should_be_local(s)
        if not should_test:
            log("No tree designation found or guessed", "")
            return "Not a local patch"

        if netdev.series_ignore_missing_tree_name(s):
            s.tree_mark_expected = None
            log('Okay to ignore lack of tree in subject, ignoring series', "")
            return "Series ignored based on subject"

        if s.tree_mark_expected:
            log_open_sec('Series should have had a tree designation')
        else:
            log_open_sec('Series okay without a tree designation')

        # TODO: make this configurable
        if "net" in self._trees and netdev.series_is_a_fix_for(s, self._trees["net"]):
            s.tree_name = "net"
        elif "net-next" in self._trees and self._trees["net-next"].check_applies(s):
            s.tree_name = "net-next"

        if s.tree_name:
            log(f"Target tree - {s.tree_name}", "")
            res = f"Guessed tree name to be {s.tree_name}"
        else:
            log("Target tree not found", "")
            res = "Guessing tree name failed - patch did not apply"
        log_end_sec()

        return res

    def series_determine_tree(self, s: PwSeries) -> str:
        log_open_sec('Determining the tree')
        try:
            ret = self._series_determine_tree(s)
        finally:
            log_end_sec()

        return ret

    def _process_series(self, pw_series) -> None:
        if pw_series['id'] in self.seen_series:
            log(f"Already seen {pw_series['id']}", "")
            return

        s = PwSeries(self._pw, pw_series)

        log("Series info",
            f"Series ID {s['id']}\n" + f"Series title {s['name']}\n" +
            f"Author {s['submitter']['name']}\n" + f"Date {s['date']}")
        log_open_sec('Patches')
        for p in s['patches']:
            log(p['name'], "")
        log_end_sec()

        if not s['received_all']:
            raise IncompleteSeries

        comment = self.series_determine_tree(s)
        s.need_async = netdev.series_needs_async(s)
        if s.need_async:
            comment += ', async'

        if hasattr(s, 'tree_name') and s.tree_name:
            s.tree_selection_comment = comment
            self._workers[s.tree_name].queue.put(s)
        else:
            core.write_tree_selection_result(self.result_dir, s, comment)
            core.mark_done(self.result_dir, s)

        self.seen_series.add(s['id'])

    def process_series(self, pw_series) -> None:
        log_open_sec(f"Checking series {pw_series['id']} with {pw_series['total']} patches")
        try:
            self._process_series(pw_series)
        finally:
            log_end_sec()

    def run(self) -> None:
        partial_series = {}

        prev_big_scan = datetime.datetime.fromtimestamp(self._state['last_poll'])
        prev_req_time = datetime.datetime.utcnow()

        # We poll every 2 minutes, for series from last 10 minutes
        # Every 3 hours we do a larger check of series of last 12 hours to make sure we didn't miss anything
        # apparently patchwork uses the time from the email headers and people back date their emails, a lot
        # We keep a history of the series we've seen in and since the last big poll to not process twice
        try:
            while True:
                this_poll_seen = set()
                req_time = datetime.datetime.utcnow()

                # Decide if this is a normal 4 minute history poll or big scan of last 12 hours
                if prev_big_scan + datetime.timedelta(hours=3) < req_time:
                    big_scan = True
                    since = prev_big_scan - datetime.timedelta(hours=9)
                    log_open_sec(f"Big scan of last 12 hours at {req_time} since {since}")
                else:
                    big_scan = False
                    since = prev_req_time - datetime.timedelta(minutes=10)
                    log_open_sec(f"Checking at {req_time} since {since}")

                json_resp = self._pw.get_series_all(since=since)
                log(f"Loaded {len(json_resp)} series", "")

                had_partial_series = False
                for pw_series in json_resp:
                    try:
                        self.process_series(pw_series)
                        this_poll_seen.add(pw_series['id'])
                    except IncompleteSeries:
                        partial_series.setdefault(pw_series['id'], 0)
                        if partial_series[pw_series['id']] < 5:
                            had_partial_series = True
                        partial_series[pw_series['id']] += 1

                if big_scan:
                    prev_req_time = req_time
                    prev_big_scan = req_time
                    # Shorten the history of series we've seen to just the last 12 hours
                    self.seen_series = this_poll_seen
                    self.done_series &= self.seen_series
                elif had_partial_series:
                    log("Partial series, not moving time forward", "")
                else:
                    prev_req_time = req_time

                # Unleash all workers
                log("Activate workers", "")
                self._barrier.wait()
                # Wait for workers to come back
                log("Wait for workers", "")
                self._barrier.wait()

                while not self._done_queue.empty():
                    s = self._done_queue.get()
                    self.done_series.add(s['id'])
                    log(f"Testing complete for series {s['id']}", "")

                secs = 120 - (datetime.datetime.utcnow() - req_time).total_seconds()
                if secs > 0:
                    log("Sleep", secs)
                    time.sleep(secs)
                log_end_sec()
                if os.path.exists('poller.quit'):
                    os.remove('poller.quit')
                    break
        finally:
            log_open_sec(f"Stopping threads")
            self._barrier.abort()
            for _, worker in self._workers.items():
                worker.should_die = True
                worker.queue.put(None)
            for _, worker in self._workers.items():
                log(f"Waiting for worker {worker.tree.name} / {worker.name}")
                worker.join()
            log_end_sec()

            self._state['last_poll'] = prev_big_scan.timestamp()
            self._state['done_series'] = list(self.seen_series)
            # Dump state
            with open('poller.state', 'w') as f:
                json.dump(self._state, f)


if __name__ == "__main__":
    os.umask(0o002)
    poller = PwPoller()
    poller.run()
