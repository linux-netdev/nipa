#!/usr/bin/env python3
# SPDX-License-Identifier: ((GPL-2.0 WITH Linux-syscall-note) OR BSD-3-Clause)

import datetime
import lzma
import os
import re
import sys
import subprocess
import time
import json


char_filter = re.compile(r'["<>&;\n]+')


def kv_to_dict(lines):
    data = {}
    for line in lines:
        entry = line.split("=", 1)
        if len(entry) < 2:
            continue
        data[entry[0]] = entry[1]
    return data


def add_one_service(result, name):
    lines = subprocess.check_output(["systemctl", "show", name]).decode('utf-8').split('\n')
    data = kv_to_dict(lines)
    keys = ['CPUUsageNSec', 'MemoryCurrent', 'ActiveState', 'SubState', 'TasksCurrent', 'TriggeredBy', 'Result']
    filtered = {}
    for k in keys:
        filtered[k] = data.get(k, 0)
    result['services'][name] = filtered


def add_one_tree(result, pfx, name):
    global char_filter

    with open(os.path.join(pfx, name), 'r') as fp:
        lines = fp.readlines()
    last = None
    test = ''
    blog = ''
    progress = ''
    for line in lines:
        if 'Testing patch' in line:
            patch = line[line.find('Testing patch') + 14:]
            progress = patch[:patch.find('|')]
            patch = patch[patch.find('|') + 2:]
            last = re.sub(char_filter, "", patch)
            test = ''
        elif 'Running test ' in line:
            test = line[17:].strip()
        elif 'Tester commencing ' in line:
            blog = line[35:].strip()
        if 'Checking barrier' in line:
            last = None
            progress = ''
            test = ''
            blog = ''
    result['runners'][name] = {"patch": last, "progress": progress, "test": test, "backlog": blog}


def add_one_runtime(fname, total, res):

    if fname.endswith('.xz'):
        with lzma.open(fname) as fp:
            lines = fp.readlines()
    else:
        with open(fname, 'r') as fp:
            lines = fp.readlines()

    t = {"start": None, "end": None}
    test = None
    cont = None
    for line in lines:
        if isinstance(line, bytes):
            line = line.decode('utf-8')

        if cont:
            t[cont] = line.strip()

            if cont == "end":
                if test and t["start"]:
                    comb = datetime.datetime.combine
                    today = datetime.date.today()
                    start = comb(today, datetime.time.fromisoformat(t["start"]))
                    end = comb(today, datetime.time.fromisoformat(t["end"]))
                    if end < start:
                        start -= datetime.timedelta(days=1)
                    sec = (end - start).total_seconds()
                    if test not in res:
                        res[test] = {"cnt": 0, "sum": 0}
                    res[test]["cnt"] += 1
                    res[test]["sum"] += sec
                    total += sec

                test = None
        cont = None

        if '* Running test ' in line:
            test = line[line.find("Running test") + 13:].strip()
            t = {"start": None, "end": None}
        elif '*** START' in line:
            cont = "start"
        elif '*** END' in line:
            cont = "end"

    return total, res


def add_runtime(result, cfg):
    reg = re.compile(cfg["regex"])

    total = 0
    res = {}

    for f in os.listdir(cfg["path"]):
        if not reg.match(f):
            continue
        fname = os.path.join(cfg["path"], f)
        if time.time() - os.path.getmtime(fname) > (5 * 24 * 60 * 60):
            continue

        print("Building runtime from log", fname)
        total, res = add_one_runtime(fname, total, res)

    res = {k: {"pct": res[k]["sum"] / total * 100, "avg": res[k]["sum"] / res[k]["cnt"]} for k in res}
    return res


def main():
    with open(sys.argv[1], 'r') as fp:
        cfg = json.load(fp)

    run_logs = True
    if os.path.isfile(sys.argv[2]):
        with open(sys.argv[2], 'r') as fp:
            prev = json.load(fp)
        if "log-files" in prev and "prev-date" in prev["log-files"]:
            prev_date = datetime.datetime.fromisoformat(prev["log-files"]["prev-date"])
            run_logs = datetime.datetime.now() - prev_date > datetime.timedelta(hours=3)
            print("Since log scan", datetime.datetime.now() - prev_date, "Will rescan:", run_logs)
            prev_date = prev["log-files"]["prev-date"]
            log_files = {"prev-date": prev_date, "data": prev["log-files"]["data"]}
    if run_logs:
        prev_date = datetime.datetime.now().isoformat()
        log_files = {"prev-date": prev_date, }

    result = {'services': {}, 'runners': {},
              'date': datetime.datetime.now().isoformat(),
              "log-files": log_files}
    for name in cfg["trees"]:
        add_one_tree(result, cfg["tree-path"], name)
    if "log-files" in cfg and run_logs:
        res = add_runtime(result, cfg["log-files"])
        result["log-files"]["data"] = res
    for name in cfg["services"]:
        add_one_service(result, name)

    with open(sys.argv[2], 'w') as fp:
        json.dump(result, fp)


if __name__ == "__main__":
    main()
