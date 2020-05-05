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

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from core import Tester
from core import Tree
from pw import Patchwork
from pw import PwSeries
import netdev


# Init state

config = configparser.ConfigParser()
config.read(['nipa.config', 'pw.config', 'poller.config'])

log_init(config.get('log', 'type', fallback='org'),
         config.get('log', 'file', fallback=os.path.join(NIPA_DIR,
                                                         "poller.org")))

state = {
    'last_poll': str(datetime.datetime.utcnow() - datetime.timedelta(hours=2)),
    'last_id': 0,
}

trees = {
    "net-next": Tree("net-next", "net-next", "../net-next", "net-next"),
    "net": Tree("net", "net", "../net", "net"),
}

tester = Tester(config.get('results', 'dir',
                           fallback=os.path.join(NIPA_DIR, "results")))

# Read the state file
try:
    with open('poller.state', 'r') as f:
        loaded = json.load(f)

        for k in state.keys():
            state[k] = loaded[k]
except FileNotFoundError:
    pass

# Prep
pw = Patchwork(config)

partial_series = 0
partial_series_id = 0
prev_time = state['last_poll']

# Loop
try:
    while True:
        poll_ival = 120
        prev_time = state['last_poll']
        prev_time_obj = datetime.datetime.fromisoformat(prev_time)
        since = prev_time_obj - datetime.timedelta(minutes=4)
        state['last_poll'] = str(datetime.datetime.utcnow())

        log_open_sec(f"Checking at {state['last_poll']} since {since}")

        json_resp = pw.get_series_all(since=since)
        log(f"Loaded {len(json_resp)} series", "")

        pw_series = {}
        for pw_series in json_resp:
            log_open_sec(f"Checking series {pw_series['id']} " +
                         f"with {pw_series['total']} patches")

            if pw_series['id'] <= state['last_id']:
                log(f"Already seen {pw_series['id']}", "")
                log_end_sec()
                continue

            s = PwSeries(pw, pw_series)

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
                if partial_series < 4 or partial_series_id != s['id']:
                    log("Partial series, retrying later", "")
                    try:
                        series_time = datetime.datetime.fromisoformat(s['date'])
                        state['last_poll'] = \
                            str(series_time - datetime.timedelta(minutes=4))
                    except:
                        state['last_poll'] = prev_time
                    poll_ival = 30
                    log_end_sec()
                    break
                else:
                    log("Partial series, happened too many times, ignoring", "")
                    log_end_sec()
                    continue

            log_open_sec('Determining the tree')
            s.tree_name = netdev.series_tree_name_direct(s)
            s.tree_marked = False
            if s.tree_name:
                log(f'Series is clearly designated for: {s.tree_name}', "")
                s.tree_marked = True
            elif netdev.series_tree_name_should_be_local(s):
                if netdev.series_ignore_missing_tree_name(s):
                    log('Okay to ignore lack of tree in subject', "")
                else:
                    log_open_sec('Series should have had a tree designation')
                    if netdev.series_is_a_fix_for(s, trees["net"]):
                        s.tree_name = "net"
                    elif trees["net-next"].check_applies(s):
                        s.tree_name = "net-next"

                    if s.tree_name:
                        log(f"Target tree - {s.tree_name}", "")
                    else:
                        log("Target tree not found", "")
                    log_end_sec()
            else:
                log("No tree designation found or guessed", "")
            log_end_sec()

            if s.tree_name:
                series_ret, patch_ret = \
                    tester.test_series(trees[s.tree_name], s)
            log_end_sec()

            state['last_id'] = s['id']

        if state['last_poll'] == prev_time:
            partial_series += 1
            partial_series_id = pw_series['id']
        else:
            partial_series = 0

        time.sleep(poll_ival)
        log_end_sec()
finally:
    # We may have not completed the last poll
    state['last_poll'] = prev_time
    # Dump state
    with open('poller.state', 'w') as f:
        loaded = json.dump(state, f)
