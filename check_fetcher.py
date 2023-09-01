#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import json
import os

from core import NIPA_DIR
from core import Maintainers, Person
from core import log, log_open_sec, log_end_sec, log_init
from core import Tree
from pw import Patchwork


def load_old_db(tgt_json):
    # Returns DB, map[patch -> state]
    try:
        with open(tgt_json, "r") as fp:
            old_db = json.load(fp)
    except FileNotFoundError:
        return [], {}

    old_pstate = {}
    for row in old_db:
        old_pstate[row["id"]] = row["state"]

    return old_db, old_pstate


def main():
    # Init state
    global config
    config = configparser.ConfigParser()
    config.read(['nipa.config', 'pw.config', 'checks.config'])

    log_init(config.get('log', 'type', fallback='org'),
             config.get('log', 'file', fallback=os.path.join(NIPA_DIR, "checks.org")),
             force_single_thread=True)

    rdir = config.get('dirs', 'results', fallback=os.path.join(NIPA_DIR, "results"))
    tgt_json = os.path.join(rdir, "checks.json")

    # Time bounds
    retain_history_days = 60         # how much data we want in the JSON
    look_back_days = 8               # oldest patch we may change state of
    expect_checks_stable_hours = 50  # oldest patch where checks themselves may change
    delegate = "netdev"

    pw = Patchwork(config)

    old_db, old_pstate = load_old_db(tgt_json)

    now = datetime.datetime.utcnow()
    since = now - datetime.timedelta(days=look_back_days)

    json_resp = pw.get_patches_all(delegate=delegate, since=since)
    jdb = []
    old_unchanged = 0
    seen_pids = set()
    for p in json_resp:
        pdate = datetime.datetime.fromisoformat(p["date"])
        hours_old = (now - pdate).total_seconds() // 3600
        # Checks won't get updated after 2+ days, so if the state is the same - skip
        if hours_old > expect_checks_stable_hours and \
                p["id"] in old_pstate and p["state"] == old_pstate[p["id"]]:
            old_unchanged += 1
            continue

        seen_pids.add(p["id"])
        checks = pw.request(p["checks"])
        for c in checks:
            info = {
                "id": p["id"],
                "date": p["date"],
                "author": p["submitter"]["name"],
                "state": p["state"],
                "delegate": p["delegate"]["username"],
                "check": c["context"],
                "result": c["state"],
                "description": c["description"]
            }
            jdb.append(info)

    new_db = []
    skipped = 0
    horizon_gc = 0
    old_stayed = 0
    for row in old_db:
        pdate = datetime.datetime.fromisoformat(row["date"])
        days_old = (now - pdate).days
        if days_old > retain_history_days:
            horizon_gc += 1
            continue
        if row["id"] in seen_pids:
            skipped += 1
            continue
        old_stayed += 1
        new_db.append(row)
    new_db += jdb
    print(f'Old db: {len(old_db)}, retained: {old_stayed}')
    print(f'Fetching: patches: {len(json_resp)}, patches old-unchanged: {old_unchanged}, checks fetched: {len(jdb)}')
    print(f'Writing:  refreshed: {skipped}, new: {len(new_db) - old_stayed}, expired: {horizon_gc} new len: {len(new_db)}')

    with open(tgt_json, "w") as fp:
        json.dump(new_db, fp)


if __name__ == "__main__":
    main()
