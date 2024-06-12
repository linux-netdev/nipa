#!/usr/bin/env python3
# SPDX-License-Identifier: ((GPL-2.0 WITH Linux-syscall-note) OR BSD-3-Clause)

import datetime
import lzma
import os
import psycopg2
import re
import requests
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
    keys = ['CPUUsageNSec', 'MemoryCurrent', 'ActiveState', 'SubState', 'TasksCurrent',
            'TriggeredBy', 'Result', 'ExecMainStartTimestampMonotonic',
            'ExecMainExitTimestampMonotonic']
    filtered = {}
    for k in keys:
        filtered[k] = data.get(k, 0)
    result['services'][name] = filtered
    result['time-mono'] = time.monotonic_ns() // 1000


def pre_strip(line, needle):
    return line[line.find(needle) + len(needle):].strip()


def add_one_tree(result, pfx, name):
    global char_filter

    with open(os.path.join(pfx, name), 'r') as fp:
        lines = fp.readlines()
    last = None
    test = ''
    test_prog = ''
    blog = ''
    progress = ''
    for line in lines:
        if 'Testing patch' in line:
            patch = pre_strip(line, 'Testing patch')

            test_sep = patch.find('|')
            patch_sep = patch.find('|', test_sep + 1)

            test_prog = patch[:test_sep]
            progress = patch[test_sep + 1:patch_sep]
            patch = patch[patch_sep + 2:]
            last = re.sub(char_filter, "", patch)
            test = ''
        elif '* Testing pull request' in line:
            patch = pre_strip(line, 'Testing pull request')
            last = re.sub(char_filter, "", patch)
            progress = '1/1'
        elif '* Test-applying' in line:
            last = pre_strip(line, 'Test-applying')
            progress = 'Series'
        elif 'Running test ' in line:
            test = pre_strip(line, 'Running test')
        elif 'Tester commencing ' in line:
            blog = line[35:].strip()
        if 'Tester done processing' in line:
            last = None
            progress = ''
            test = ''
            test_prog = ''
            blog = ''
    result['runners'][name] = {"patch": last,
                               "progress": progress,
                               "test": test,
                               "test-progress": test_prog,
                               "backlog": blog}


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


def add_remote_services(result, remote):
    r = requests.get(remote['url'])
    data = json.loads(r.content.decode('utf-8'))
    result["remote"][remote["name"]] = data


def add_db(result, cfg):
    db_name = cfg["db"]["name"]
    tbl = cfg["db"]["table"]

    psql = psycopg2.connect(database=db_name)
    psql.autocommit = True

    with psql.cursor() as cur:
        cur.execute(f"SELECT pg_database_size('{db_name}')")
        size = cur.fetchall()[0][0]
        print("DB size", size)

        arg = cur.mogrify("(NOW(),%s)", (size, ))
        cur.execute(f"INSERT INTO {tbl}(ts, size) VALUES" + arg.decode('utf-8'))

    with psql.cursor() as cur:
        cur.execute(f"SELECT ts,size FROM {tbl} ORDER BY id DESC LIMIT 40")
        result["db"]["data"] = [ {'ts': t.isoformat(), 'size': s} for t, s in reversed(cur.fetchall()) ]


def main():
    with open(sys.argv[1], 'r') as fp:
        cfg = json.load(fp)

    log_files = {}
    run_logs = 'log-files' in cfg

    db = {}
    run_db = 'db' in cfg

    if os.path.isfile(sys.argv[2]):
        with open(sys.argv[2], 'r') as fp:
            prev = json.load(fp)

        if "log-files" in prev and "prev-date" in prev["log-files"]:
            prev_date = datetime.datetime.fromisoformat(prev["log-files"]["prev-date"])
            run_logs = datetime.datetime.now() - prev_date > datetime.timedelta(hours=3)
            print("Since log scan", datetime.datetime.now() - prev_date, "Will rescan:", run_logs)
            prev_date = prev["log-files"]["prev-date"]
            log_files = {"prev-date": prev_date, "data": prev["log-files"]["data"]}

        if "db" in prev and "prev-date" in prev["db"]:
            prev_date = datetime.datetime.fromisoformat(prev["db"]["prev-date"])
            run_db = datetime.datetime.now() - prev_date > datetime.timedelta(hours=24)
            print("Since db monitor", datetime.datetime.now() - prev_date, "Will rescan:", run_db)
            prev_date = prev["db"]["prev-date"]
            db = {"prev-date": prev_date, "data": prev["db"]["data"]}

    if run_logs:
        prev_date = datetime.datetime.now().isoformat()
        log_files = {"prev-date": prev_date, }

    if run_db:
        prev_date = datetime.datetime.now().isoformat()
        db = {"prev-date": prev_date, }

    result = {'services': {}, 'runners': {}, 'remote': {},
              'date': datetime.datetime.now().isoformat(),
              "log-files": log_files,
              "db": db}
    if "trees" in cfg:
        for name in cfg["trees"]:
            add_one_tree(result, cfg["tree-path"], name)
    if "log-files" in cfg and run_logs:
        res = add_runtime(result, cfg["log-files"])
        result["log-files"]["data"] = res
    for name in cfg["services"]:
        add_one_service(result, name)

    if "remote" in cfg:
        for remote in cfg["remote"]:
            add_remote_services(result, remote)

    if "db" in cfg and run_db:
        add_db(result, cfg)

    with open(sys.argv[2], 'w') as fp:
        json.dump(result, fp)


if __name__ == "__main__":
    main()
