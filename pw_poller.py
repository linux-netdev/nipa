#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.
# Copyright (c) 2020 Facebook

import configparser
import datetime
import json
import os
import time
from typing import Dict

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from core import Tester
from core import Tree
from pw import Patchwork
from pw import PwSeries
import netdev


class IncompleteSeries(Exception):
    pass


class PwPoller:
    def __init__(self) -> None:
        config = configparser.ConfigParser()
        config.read(['nipa.config', 'pw.config', 'poller.config'])

        log_init(config.get('log', 'type', fallback='org'),
                 config.get('log', 'file', fallback=os.path.join(NIPA_DIR,
                                                                 "poller.org")))

        # TODO: make this non-static / read from a config
        self._trees = {
            "net-next": Tree("net-next", "net-next", "../net-next", "net-next"),
            "net": Tree("net", "net", "../net", "net"),
        }

        self._tester = Tester(config.get('results', 'dir',
                                         fallback=os.path.join(NIPA_DIR, "results")))

        self._pw = Patchwork(config)

        self._state = {
            'last_poll': (datetime.datetime.utcnow() - datetime.timedelta(hours=2)).timestamp(),
            'last_id': 0,
        }
        self.init_state_from_disk()

    def init_state_from_disk(self) -> None:
        try:
            with open('poller.state', 'r') as f:
                loaded = json.load(f)

                for k in self._state.keys():
                    self._state[k] = loaded[k]
        except FileNotFoundError:
            pass

    def series_determine_tree(self, s: PwSeries) -> str:
        log_open_sec('Determining the tree')
        s.tree_name = netdev.series_tree_name_direct(s)
        s.tree_mark_expected = True
        s.tree_marked = bool(s.tree_name)

        if s.tree_name:
            log(f'Series is clearly designated for: {s.tree_name}', "")
            log_end_sec()
            return f"Clearly marked for {s.tree_name}"

        s.tree_mark_expected = netdev.series_tree_name_should_be_local(s)
        if s.tree_mark_expected == False:
            log("No tree designation found or guessed", "")
            log_end_sec()
            return "Not a local patch"

        if netdev.series_ignore_missing_tree_name(s):
            s.tree_mark_expected = None
            log('Okay to ignore lack of tree in subject, ignoring series', "")
            log_end_sec()
            return "Series ignored based on subject"

        log_open_sec('Series should have had a tree designation')
        if netdev.series_is_a_fix_for(s, self._trees["net"]):
            s.tree_name = "net"
        elif self._trees["net-next"].check_applies(s):
            s.tree_name = "net-next"

        if s.tree_name:
            log(f"Target tree - {s.tree_name}", "")
            res = f"Guessed tree name to be {s.tree_name}"
        else:
            log("Target tree not found", "")
            res = "Guessing tree name failed - patch did not apply"
        log_end_sec()

        log_end_sec()
        return res

    def process_series(self, pw_series) -> None:
        log_open_sec(f"Checking series {pw_series['id']} " +
                     f"with {pw_series['total']} patches")

        if pw_series['id'] <= self._state['last_id']:
            log(f"Already seen {pw_series['id']}", "")
            log_end_sec()
            return

        s = PwSeries(self._pw, pw_series)

        log("Series info",
            f"Series ID {s['id']}\n" +
            f"Series title {s['name']}\n" +
            f"Author {s['submitter']['name']}\n" +
            f"Date {s['date']}")
        log_open_sec('Patches')
        for p in s['patches']:
            log(p['name'], "")
        log_end_sec()

        if not s['received_all']:
            raise IncompleteSeries

        comment = self.series_determine_tree(s)

        if hasattr(s, 'tree_name') and s.tree_name:
            series_ret, patch_ret = \
                self._tester.test_series(self._trees[s.tree_name], s)

        tree_test_dir = os.path.join(self._tester.result_dir, str(s.id), "tree_selection")
        if not os.path.exists(tree_test_dir):
            os.makedirs(tree_test_dir)

        with open(os.path.join(tree_test_dir, "retcode"), "w+") as fp:
            fp.write("0")
        with open(os.path.join(tree_test_dir, "desc"), "w+") as fp:
            fp.write(comment)

        done_file = os.path.join(self._tester.result_dir, str(s.id), ".tester_done")
        if not os.path.exists(done_file):
            os.mknod(done_file)

        log_end_sec()

        self._state['last_id'] = s['id']

    def run(self) -> None:
        partial_series = 0
        partial_series_id = 0
        prev_time = self._state['last_poll']

        # Loop
        try:
            while True:
                # TODO: keep series selection as a set, and check for series from last 12 hours every 3 hours
                poll_ival = 120
                prev_time = self._state['last_poll']
                prev_time_obj = datetime.datetime.fromtimestamp(prev_time)
                since = prev_time_obj - datetime.timedelta(minutes=4)
                self._state['last_poll'] = datetime.datetime.utcnow().timestamp()

                log_open_sec(f"Checking at {self._state['last_poll']} since {since}")

                json_resp = self._pw.get_series_all(since=since)
                log(f"Loaded {len(json_resp)} series", "")

                pw_series = {}
                for pw_series in json_resp:
                    try:
                        self.process_series(pw_series)
                    except IncompleteSeries:
                        if partial_series < 10 or partial_series_id != pw_series['id']:
                            log("Partial series, retrying later", "")
                            try:
                                series_time = datetime.datetime.strptime(pw_series['date'], '%Y-%m-%dT%H:%M:%S')
                                self._state['last_poll'] = \
                                    (series_time - datetime.timedelta(minutes=4)).timestamp()
                            except:
                                self._state['last_poll'] = prev_time
                            poll_ival = 30
                            log_end_sec()
                            break
                        else:
                            log("Partial series, happened too many times, ignoring", "")
                            log_end_sec()
                            continue

                if self._state['last_poll'] == prev_time:
                    partial_series += 1
                    partial_series_id = pw_series['id']
                else:
                    partial_series = 0

                time.sleep(poll_ival)
                log_end_sec()
        finally:
            # We may have not completed the last poll
            self._state['last_poll'] = prev_time
            # Dump state
            with open('poller.state', 'w') as f:
                json.dump(self._state, f)


if __name__ == "__main__":
    poller = PwPoller()
    poller.run()
