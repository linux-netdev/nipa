#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import json
import os
import sys
import time

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from pw import Patchwork, PatchworkCheckState

"""
# in addition to NIPA's configs
[cfg]
refres=#secs
[input]
branch_info=fs/path.json
results=fs/path2.json
filters=fs/path/to/filters.json
[output]
results_by_branch=optional/dump/of/rbb
outcomes=optional/dump/of/outcomes
[state]
patch_state=state.json
[www]
contest=https://server-with-ui/contest.html
"""

class Codes:
    UNKNOWN = -2
    PENDING =  1

unreal_results = {Codes.UNKNOWN, Codes.PENDING}

str_to_code = {
    'pass': 0,
    'PASS': 0,
    'skip': 2,
    'SKIP': 2,
    'warn': 3,
    'fail': 4,
    'FAIL': 4,
    'ERROR': 4,
}
code_to_str = {
    Codes.UNKNOWN: 'unknown',
    0: 'pass',
    Codes.PENDING: 'pending',
    2: 'skip',
    3: 'warn',
    4: 'fail',
}
code_to_pw = {
    Codes.UNKNOWN: None,
    0: PatchworkCheckState.SUCCESS,
    Codes.PENDING: PatchworkCheckState.PENDING,
    2: PatchworkCheckState.WARNING,
    3: PatchworkCheckState.WARNING,
    4: PatchworkCheckState.FAIL,
}


def result_can_skip(results, entry, filters):
    for ignore in filters["ignore-results"]:
        if ("remote" not in ignore or results["remote"] == ignore["remote"]) and \
            ("executor" not in ignore or results["executor"] == ignore["executor"]) and \
            ("branch" not in ignore or results["branch"] == ignore["branch"]) and \
            ("group" not in ignore or entry["group"] == ignore["group"]) and \
            ("test" not in ignore or entry["test"] == ignore["test"]):
           return True

    return False


def results_summarize(filters: dict, results: dict) -> dict:
    if not results or not results["results"]:
        return {'result': 'pending', 'code': Codes.UNKNOWN, 'cnt': 0}

    cnt = 0
    code = 0
    for entry in results["results"]:
        test_code = str_to_code[entry["result"]]
        if test_code:
            if result_can_skip(results, entry, filters):
                continue

        code = max(code, test_code)
        cnt += 1
    return {'result': code_to_str[code], 'code': code, 'cnt': cnt}


def results_summary_combine(a, b):
    code = max(a["code"], b["code"])
    return {'result': code_to_str[code],
            'code': code,
            'cnt': a['cnt'] + b['cnt']}


def results_pivot(filters: dict, results: dict) -> dict:
    """
    results come in as a list, we want to flip them into:
    { "branch-name": {"remote-name": {"code": ...}, }, }
    """
    flipped = {}
    for entry in results:
        if entry['branch'] not in flipped:
            flipped[entry['branch']] = {}
        if entry['remote'] not in flipped[entry['branch']]:
            flipped[entry['branch']][entry['remote']] = \
                results_summarize({}, {})

        old = flipped[entry['branch']][entry['remote']]
        new = results_summarize(filters, entry)
        flipped[entry['branch']][entry['remote']] = \
            results_summary_combine(old, new)
    return flipped


def branch_summarize(filters: dict, results_by_branch: dict) -> dict:
    summary = {}
    for name, branch in results_by_branch.items():
        code = 0
        test_cnt = 0
        for remote in filters["remotes"]:
            if remote in branch:
                code = max(code, branch[remote]['code'])
                test_cnt += branch[remote]["cnt"]
            else:
                code = Codes.PENDING
        summary[name] = {'result': code_to_str[code], 'code': code, 'cnt': test_cnt}
    return summary


def result_upgrades(states: dict, item_id: str, outcome: dict, branch: str):
    # Never seen before, always report.
    if item_id not in states:
        return True

    prev = states[item_id]
    # Never upgrade to unreal.
    if outcome['code'] in unreal_results:
        return False
    # Anything is better than unreal
    if prev['code'] in unreal_results:
        return True

    # real results are min (if we pass once, we pass)
    if prev['code'] > outcome['code']:
        return True
    if prev['code'] == outcome['code']:
        return prev['cnt'] < outcome['cnt']
    return False


def patch_state_compute(state: dict, branches: dict, branch_outcome: dict) -> None:
    series_state = state["series"]
    pr_state = state["prs"]
    for name, branch in branches.items():
        # branch got tagged but faker didn't add it to results, yet
        if name not in branch_outcome:
            continue

        outcome = branch_outcome[name]
        for series_id in branch["series"]:
            # branches store IDs in a list, so they are ints
            # but in JSON dict keys can't be ints so we need
            # to consistently convert ids to strings
            series_id = str(series_id)
            if result_upgrades(series_state, series_id, outcome, name):
                series_state[series_id] = outcome.copy()
                series_state[series_id]["branch"] = name
                series_state[series_id]["update"] = True

        for pr_id in branch["prs"]:
            pr_id = str(pr_id)
            if result_upgrades(pr_state, pr_id, outcome, name):
                pr_state[pr_id] = outcome.copy()
                pr_state[pr_id]["branch"] = name
                pr_state[pr_id]["update"] = True


def skip_update(outcome) -> bool:
    if "update" not in outcome:
        return True
    if not outcome["update"]:
        del outcome["update"]
        return True
    return False


def update_one(pw, patch_id, outcome, link):
    description = outcome['branch']
    if outcome["code"] >= 0:
        description += f' (tests: {outcome["cnt"]})'
    url = link + '?pw-n=0&branch=' + outcome['branch']
    pw.post_check(patch_id, name="contest", state=code_to_pw[outcome["code"]],
                  url=url, desc=description)


def _patch_state_update(pw, state: dict, link: str):
    update_cnt = 0
    for series_id, outcome in state["series"].items():
        if skip_update(outcome):
            continue

        try:
            log_open_sec('Updating series ' + series_id)
            series_pw = pw.get("series", series_id)
            for patch in series_pw["patches"]:
                update_one(pw, patch["id"], outcome, link)
            update_cnt += 1

            del outcome["update"]
        finally:
            log_end_sec()

    for pr_id, outcome in state["prs"].items():
        if skip_update(outcome):
            continue

        try:
            log_open_sec('Updating PR ' + pr_id)
            update_one(pw, pr_id, outcome, link)
            update_cnt += 1

            del outcome["update"]
        finally:
            log_end_sec()
    if update_cnt:
        print("Updated", update_cnt, "pw things")


def patch_state_update(pw, state: dict, link: str):
    log_open_sec('Updating patch states')
    try:
        _patch_state_update(pw, state, link)
    finally:
        log_end_sec()


def main_loop(pw) -> int:
    config = parse_configs()

    try:
        with open(config.get('state', 'patch_state'), "rb") as fp:
            patch_state = json.load(fp)
    except FileNotFoundError:
        patch_state = {'series':{}, 'prs':{}}
    with open(config.get('input', 'branch_info'), "rb") as fp:
        branches = json.load(fp)
    with open(config.get('input', 'results'), "rb") as fp:
        results = json.load(fp)
    with open(config.get('input', 'filters'), "rb") as fp:
        filters = json.load(fp)

    results_by_branch = results_pivot(filters, results)
    branch_outcome = branch_summarize(filters, results_by_branch)
    patch_state_compute(patch_state, branches, branch_outcome)
    patch_state_update(pw, patch_state, config.get('www', 'contest'))

    rbb = config.get('output', 'results_by_branch', fallback=None)
    if rbb:
        with open(rbb, 'w') as fp:
            json.dump(results_by_branch, fp)
    outcomes = config.get('output', 'outcomes', fallback=None)
    if outcomes:
        with open(outcomes, 'w') as fp:
            json.dump(branch_outcome, fp)
    with open(config.get('state', 'patch_state'), 'w') as fp:
        json.dump(patch_state, fp)

    return int(config.get('cfg', 'refresh'))


def parse_configs():
    config = configparser.ConfigParser()
    config.read(['nipa.config', 'pw.config', 'contest.config'])
    return config


def main() -> None:
    config = parse_configs()

    log_init(config.get('log', 'type', fallback='org'),
             config.get('log', 'file', fallback=os.path.join(NIPA_DIR, "contest.org")),
             force_single_thread=True)

    pw = Patchwork(config)

    # We could do a file system watch here, because the inputs are all local.
    while True:
        log("Running at " + str(datetime.datetime.now()))
        delay = main_loop(pw)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
