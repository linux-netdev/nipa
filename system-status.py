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


def add_disk_size(result, path):
    output = subprocess.check_output(f"df {path} --output=avail,size".split()).decode('utf-8')
    sizes = output.split('\n')[1].split()
    sizes = [int(s) for s in sizes]
    result["disk-use"] = round(sizes[0] / sizes[1] * 100, 2)


def pre_strip(line, needle):
    return line[line.find(needle) + len(needle):].strip()


def add_one_tree(result, pfx, name):
    log_file = os.path.join(pfx, name)
    stat = os.stat(log_file)

    with open(log_file, 'r') as fp:
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
                               "backlog": blog,
                               "mtime": stat.st_mtime}


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

    # Collect runners from remote for later merging
    if "runners" in data:
        if "_remote_runners" not in result:
            result["_remote_runners"] = []
        result["_remote_runners"].append((remote["name"], data["runners"]))


def merge_runners(result):
    """Merge remote runners into result, prefixing only if multiple sources exist"""
    remote_runners = result.pop("_remote_runners", [])
    if not remote_runners:
        return

    # Count sources: local + each remote that has runners
    num_sources = (1 if result["runners"] else 0) + sum(1 for _, r in remote_runners if r)
    need_prefix = num_sources > 1

    if need_prefix and result["runners"]:
        result["runners"] = {f"local-{k}": v for k, v in result["runners"].items()}

    for remote_name, runners in remote_runners:
        prefix = f"{remote_name}-" if need_prefix else ""
        result["runners"].update({f"{prefix}{k}": v for k, v in runners.items()})


def get_metric_values(db_connection, source, category, name, limit=120):
    """ Query metrics from the DB """
    with db_connection.cursor() as cur:
        cur.execute("""
            SELECT ts, value
            FROM metrics
            WHERE source = %s AND category = %s AND name = %s
            ORDER BY ts DESC
            LIMIT %s
        """, (source, category, name, limit))
        return cur.fetchall()


def add_db(result, cfg):
    db_name = cfg["db"]["name"]

    psql = psycopg2.connect(database=db_name)
    psql.autocommit = True

    with psql.cursor() as cur:
        cur.execute(f"SELECT pg_database_size('{db_name}')")
        size = cur.fetchall()[0][0]
        print("DB size", size)

        remote_disk = 0
        for _, remote in result["remote"].items():
            remote_disk = remote["disk-use"]

        # Insert metrics data
        metrics_data = [
            ("system", "db", "size", size),
            ("system", "disk", "util", result["disk-use"]),
            ("system-metal", "disk", "util", remote_disk)
        ]

        for source, category, name, value in metrics_data:
            cur.execute(f"INSERT INTO metrics(ts, source, category, name, value) VALUES(NOW(), '{source}', '{category}', '{name}', %s)", (value,))

    # Retrieve display data - query each metric individually
    size_data = get_metric_values(psql, "system", "db", "size", limit=40)
    disk_data = get_metric_values(psql, "system", "disk", "util", limit=40)
    disk_remote_data = get_metric_values(psql, "system-metal", "disk", "util", limit=40)

    # Since they're inserted with the same timestamp, we can just zip them together
    result["db"]["data"] = [
        {
            'ts': ts.isoformat(),
            'size': size,
            'disk': disk,
            'disk_remote': disk_remote
        }
        for (ts, size), (_, disk), (_, disk_remote) in zip(size_data, disk_data, disk_remote_data)
    ]
    # Reverse to get chronological order (oldest first)
    result["db"]["data"].reverse()

    psql.close()


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

    # Merge runners from remotes (after all remotes are fetched)
    merge_runners(result)

    add_disk_size(result, "/")

    if "db" in cfg and run_db:
        add_db(result, cfg)

    with open(sys.argv[2], 'w') as fp:
        json.dump(result, fp)


if __name__ == "__main__":
    main()
