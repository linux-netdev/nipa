#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.
# Copyright (c) 2020 Facebook

import configparser
import datetime
import json
import os
import shutil
import time
import queue
from typing import Dict
from importlib import import_module

from core import NIPA_DIR
from core import NipaLifetime
from core import log, log_open_sec, log_end_sec, log_init
from core import Tester
from core import Tree
from pw import Patchwork
from pw import PwSeries
import core


class IncompleteSeries(Exception):
    pass


class PwPoller:
    def __init__(self, config) -> None:
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

        self._done_queue = queue.Queue()
        self._workers = []
        self._work_queues = {}
        for k, tree in self._trees.items():
            self._work_queues[k] = queue.Queue()

            worker_cnt = config.getint('workers', tree.name, fallback=1)
            for worker_id in range(worker_cnt):
                worker = Tester(self.result_dir, tree.work_tree(worker_id),
                                self._work_queues[k], self._done_queue)
                worker.start()
                log(f"Started worker {worker.name} for {k}")
                self._workers.append(worker)

        self._pw = Patchwork(config)

        self._state = {
            'last_event_ts': (datetime.datetime.now() -
                              datetime.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%S'),
        }
        self.init_state_from_disk()

        self._recheck_period = config.getint('poller', 'recheck_period', fallback=3)
        self._recheck_lookback = config.getint('poller', 'recheck_lookback', fallback=9)

        listmodname = config.get('list', 'module', fallback='netdev')
        self.list_module = import_module(listmodname)

    def init_state_from_disk(self) -> None:
        try:
            with open('poller.state', 'r') as f:
                loaded = json.load(f)

                for k in loaded.keys():
                    self._state[k] = loaded[k]
        except FileNotFoundError:
            pass

    def _series_determine_tree(self, s: PwSeries) -> str:
        s.tree_name = self.list_module.series_tree_name_direct(self._trees.keys(), s)
        s.tree_mark_expected = True
        s.tree_marked = bool(s.tree_name)

        if s.is_pure_pull():
            if s.title.find('-next') >= 0:
                s.tree_name = self.list_module.next_tree
            else:
                s.tree_name = self.list_module.current_tree
            s.tree_mark_expected = None
            return f"Pull request for {s.tree_name}"

        if s.tree_name:
            log(f'Series is clearly designated for: {s.tree_name}', "")
            return f"Clearly marked for {s.tree_name}"

        s.tree_mark_expected, should_test = self.list_module.series_tree_name_should_be_local(s)
        if not should_test:
            log("No tree designation found or guessed", "")
            return "Not a local patch"

        if self.list_module.series_ignore_missing_tree_name(s):
            s.tree_mark_expected = None
            log('Okay to ignore lack of tree in subject, ignoring series', "")
            return "Series ignored based on subject"

        if s.tree_mark_expected:
            log_open_sec('Series should have had a tree designation')
        else:
            log_open_sec('Series okay without a tree designation')

        if self.list_module.current_tree in self._trees and \
           self.list_module.series_is_a_fix_for(s, self._trees[self.list_module.current_tree]):
            s.tree_name = self.list_module.current_tree
        elif self.list_module.next_tree in self._trees and \
             self._trees[self.list_module.next_tree].check_applies(s):
            s.tree_name = self.list_module.next_tree

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
        s.need_async = self.list_module.series_needs_async(s)
        if s.need_async:
            comment += ', async'

        if hasattr(s, 'tree_name') and s.tree_name:
            s.tree_selection_comment = comment
            if not s.tree_name in self._work_queues:
                log(f"skip {pw_series['id']} for unknown tree {s.tree_name}", "")
                return
            self._work_queues[s.tree_name].put(s)
        else:
            core.write_tree_selection_result(self.result_dir, s, comment)
            core.mark_done(self.result_dir, s)

    def process_series(self, pw_series) -> None:
        log_open_sec(f"Checking series {pw_series['id']} with {pw_series['total']} patches")
        try:
            self._process_series(pw_series)
        finally:
            log_end_sec()

    def run(self, life) -> None:
        since = self._state['last_event_ts']

        try:
            # We poll every 2 minutes after this
            secs = 0
            while life.next_poll(secs):
                req_time = datetime.datetime.now()
                log_open_sec(f"Querying patchwork at {req_time} since {since}")
                json_resp, since = self._pw.get_new_series(since=since)
                log(f"Loaded {len(json_resp)} series", "")

                # Advance the time by 1 usec, pw does >= for time comparison
                since  = datetime.datetime.fromisoformat(since)
                since += datetime.timedelta(microseconds=1)
                since  = since.isoformat()

                for pw_series in json_resp:
                    try:
                        self.process_series(pw_series)
                    except IncompleteSeries:
                        # didn't make it to the list fully, patchwork
                        # shouldn't have had this event at all though
                        pass

                while not self._done_queue.empty():
                    s = self._done_queue.get()
                    log(f"Testing complete for series {s['id']}", "")

                secs = 120 - (datetime.datetime.now() - req_time).total_seconds()
                if secs > 0:
                    log("Sleep", secs)
                log_end_sec()
        except KeyboardInterrupt:
            pass  # finally will still run, but don't splat
        finally:
            # Dump state before trying to stop workers, in case they hang
            self._state['last_event_ts'] = since
            with open('poller.state', 'w') as f:
                json.dump(self._state, f)

            log_open_sec(f"Stopping threads")
            for worker in self._workers:
                worker.should_die = True
                worker.queue.put(None)
            for worker in self._workers:
                log(f"Waiting for worker {worker.tree.name} / {worker.name}")
                worker.join()
            log_end_sec()


if __name__ == "__main__":
    os.umask(0o002)

    config = configparser.ConfigParser()
    config.read(['nipa.config', 'pw.config', 'poller.config'])

    log_dir = config.get('log', 'dir', fallback=NIPA_DIR)
    log_init(config.get('log', 'type', fallback='org'),
             config.get('log', 'file', fallback=os.path.join(log_dir, "poller.org")))

    life = NipaLifetime(config)
    poller = PwPoller(config)
    poller.run(life)
    life.exit()
