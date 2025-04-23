#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0


from flask import Flask
from flask import Response
from flask import request
import json
import psycopg2
import os
import re
import datetime


app = Flask("NIPA contest query")

db_name = os.getenv('DB_NAME')
psql = psycopg2.connect(database=db_name)
psql.autocommit = True


@app.route('/')
def hello():
    return '<h1>boo!</h1>'


@app.route('/branches')
def branches():
    with psql.cursor() as cur:
        cur.execute(f"SELECT branch, t_date, base, url FROM branches ORDER BY t_date DESC LIMIT 40")
        rows = [{"branch": r[0], "date": r[1].isoformat() + "+00:00", "base": r[2], "url": r[3]} for r in cur.fetchall()]
        rows.reverse()
    return rows


def branches_to_rows(br_cnt, remote, br_pfx=None):
    cnt = 0
    with psql.cursor() as cur:
        remote_k = ",remote" if remote else ""
        # Slap the -2 in here as the first letter of the date, to avoid prefix of prefix matches
        pfx_flt = f"WHERE branch LIKE '{br_pfx}-2%' " if br_pfx else ""

        q = f"SELECT branch,count(*),branch_date{remote_k} FROM results {pfx_flt} GROUP BY branch,branch_date{remote_k} ORDER BY branch_date DESC LIMIT {br_cnt}"

        cur.execute(q)
        for r in cur.fetchall():
            cnt += r[1]
    return cnt


def result_as_l2(raw):
    row = json.loads(raw)
    flat = []

    for l1 in row["results"]:
        if "results" not in l1:
            flat.append(l1)
        else:
            for case in l1["results"]:
                data = l1.copy()
                del data["results"]
                if "time" in data:
                    del data["time"]
                # in case of retry, the subtest might not have been re-executed
                if "retry" in data:
                    del data["retry"]
                data |= case
                data["test"] = l1["test"] + '.' + case["test"]
                flat.append(data)
    row["results"] = flat
    return json.dumps(row)


@app.route('/results')
def results():
    limit = 0
    where = []
    log = ""

    form = request.args.get('format')
    remote = request.args.get('remote')
    if remote and re.match(r'^[\w_ -]+$', remote) is None:
        remote = None

    br_name = request.args.get('branch-name')
    if br_name:
        if re.match(r'^[\w_ -]+$', br_name) is None:
            return {}

        br_cnt = br_name
        limit = 100
        where.append(f"branch = '{br_name}'")
        t1 = t2 = datetime.datetime.now()
    else:
        t1 = datetime.datetime.now()

        br_cnt = request.args.get('branches')
        try:
            br_cnt = int(br_cnt)
        except:
            br_cnt = None
        if not br_cnt:
            br_cnt = 10

        br_pfx = request.args.get('br-pfx')
        if br_pfx:
            # Slap the -2 in here as the first letter of the date, to avoid prefix of prefix matches
            where.append(f"branch LIKE '{br_pfx}-2%'")

        limit = branches_to_rows(br_cnt, remote, br_pfx)

        t2 = datetime.datetime.now()

    if remote:
        where.append(f"remote = '{remote}'")
        log += ', remote'

    where = "WHERE " + " AND ".join(where) if where else ""

    if not form or form == "normal":
        with psql.cursor() as cur:
            cur.execute(f"SELECT json_normal FROM results {where} ORDER BY branch_date DESC LIMIT {limit}")
            rows = "[" + ",".join([r[0] for r in cur.fetchall()]) + "]"
    elif form == "l2":
        with psql.cursor() as cur:
            cur.execute(f"SELECT json_normal, json_full FROM results {where} ORDER BY branch_date DESC LIMIT {limit}")
            rows = "["
            for r in cur.fetchall():
                if rows[-1] != '[':
                    rows += ','
                if r[1] and len(r[1]) > 50:
                    rows += result_as_l2(r[1])
                else:
                    rows += r[0]
            rows += ']'
        log += ', l2'
    else:
        rows = "[]"

    t3 = datetime.datetime.now()
    print(f"Query for {br_cnt} branches, {limit} records{log} took: {str(t3-t1)} ({str(t2-t1)}+{str(t3-t2)})")

    return Response(rows, mimetype='application/json')


@app.route('/remotes')
def remotes():
    t1 = datetime.datetime.now()

    with psql.cursor() as cur:
        cur.execute(f"SELECT remote FROM results GROUP BY remote LIMIT 50")
        rows = [r[0] for r in cur.fetchall()]

    t2 = datetime.datetime.now()
    print(f"Query for remotes: {str(t2-t1)}")

    return rows
