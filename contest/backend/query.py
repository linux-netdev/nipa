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

# How many branches to query to get flakes for last month
flake_cnt = 300


@app.route('/')
def hello():
    return '<h1>boo!</h1>'


@app.route('/branches')
def branches():
    with psql.cursor() as cur:
        cur.execute("SELECT branch, t_date, base, url FROM branches ORDER BY t_date DESC LIMIT 40")
        rows = [{"branch": r[0], "date": r[1].isoformat() + "+00:00", "base": r[2], "url": r[3]} for r in cur.fetchall()]
        rows.reverse()
    return rows


def get_oldest_branch_date(br_cnt, br_pfx=None):
    """
    Find the branch_date of the oldest branch that should be included
    based on the requested number of branches.
    Returns the cutoff date string or None if no limit should be applied.
    """
    with psql.cursor() as cur:
        # Slap the -2 in here as the first letter of the date,
        # to avoid prefix of prefix matches
        pfx_flt = f"WHERE branch LIKE '{br_pfx}-2%' " if br_pfx else ""

        order_limit = f"ORDER BY branch_date DESC LIMIT {br_cnt}"

        # Get unique branch dates from both tables, ordered by date descending
        # We use UNION to combine unique branch_dates from both tables
        # Make sure to limit both sides to avoid a huge merge
        query = f"""
        (SELECT DISTINCT branch_date FROM results {pfx_flt} {order_limit})
        UNION
        (SELECT DISTINCT branch_date FROM results_pending {pfx_flt} {order_limit})
        {order_limit}
        """

        cur.execute(query)
        rows = cur.fetchall()

        if len(rows) < br_cnt:
            # DB doesn't have enough data, no need to limit
            return None

        # Return the oldest branch_date from our limit
        return rows[-1][0]  # Last row is the oldest due to DESC order


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
    pending = request.args.get('pending') in {'1', 'y', 'yes', 'true'}
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
        except (TypeError, ValueError):
            br_cnt = None
        if not br_cnt:
            br_cnt = 10

        br_pfx = request.args.get('br-pfx')
        if br_pfx:
            # Slap the -2 in here as the first letter of the date, to avoid prefix of prefix matches
            where.append(f"branch LIKE '{br_pfx}-2%'")

        # Get the cutoff date for the requested number of branches
        cutoff_date = get_oldest_branch_date(br_cnt, br_pfx)
        if cutoff_date:
            where.append(f"branch_date >= '{cutoff_date}'")

        t2 = datetime.datetime.now()

        # Set a reasonable limit to prevent runaway queries
        limit = 10000

    if remote:
        where.append(f"remote = '{remote}'")
        log += ', remote'

    where = "WHERE " + " AND ".join(where) if where else ""

    if not form or form == "normal":
        with psql.cursor() as cur:
            cur.execute(f"SELECT json_normal FROM results {where} ORDER BY branch_date DESC LIMIT {limit}")
            all_rows = [r[0] for r in cur.fetchall()]

            if pending:
                # Get pending results from results_pending table
                cur.execute(f"""
                    SELECT json_build_object(
                        'branch', branch,
                        'remote', remote,
                        'executor', executor,
                        'start', (t_start AT TIME ZONE 'UTC')::text,
                        'end', null,
                        'results', null
                    )::text
                    FROM results_pending {where} ORDER BY branch_date DESC LIMIT {limit}
                """)
                all_rows += [r[0] for r in cur.fetchall()]
            rows = "[" + ",".join(all_rows) + "]"
    elif form == "l2":
        with psql.cursor() as cur:
            # Get completed results only, pending + l2 makes no sense
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
        cur.execute("SELECT remote FROM results GROUP BY remote LIMIT 50")
        rows = [r[0] for r in cur.fetchall()]

    t2 = datetime.datetime.now()
    print(f"Query for remotes: {str(t2-t1)}")

    return rows


@app.route('/stability')
def stability():
    # auto = query only tests which NIPA ignores based on stability
    auto = request.args.get('auto')

    where = ""
    if auto == "y" or auto == '1' or auto == 't':
        where = "WHERE autoignore = true"
    elif auto == "n" or auto == '0' or auto == 'f':
        where = "WHERE autoignore = false"

    with psql.cursor() as cur:
        cur.execute(f"SELECT * FROM stability {where}")

        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        # Convert each row to a dictionary with column names as keys
        data = [{columns[i]: value for i, value in enumerate(row)} for row in rows]

    return data


@app.route('/device-info')
def dev_info():
    with psql.cursor() as cur:
        cur.execute("SELECT * FROM devices_info")

        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        # Convert each row to a dictionary with column names as keys
        data = [{columns[i]: value for i, value in enumerate(row)} for row in rows]

    return data


@app.route('/flaky-tests')
def flaky_tests():
    """
    Returns tests that are flaky (first try fails, retry passes, and no crash).
    """
    global flake_cnt
    limit = request.args.get('limit')
    try:
        limit = int(limit)
        month = False
    except (TypeError, ValueError):
        month = True # Default to querying last month
        limit = flake_cnt  # Default limit

    # Find branches with incomplete results, psql JSON helpers fail for them
    t = datetime.datetime.now()
    with psql.cursor() as cur:
        query = """
        SELECT branch
        FROM results
        WHERE json_normal NOT LIKE '%"results": [%'
        GROUP BY branch;
        """

        cur.execute(query)
        rows = cur.fetchall()
        branches = ""
        if rows:
            branches = " AND branch != ".join([""] + [f"'{r[0]}'" for r in rows])
    print(f"Query for in-prog execs took: {str(datetime.datetime.now() - t)}")

    t = datetime.datetime.now()
    with psql.cursor() as cur:
        # Query for tests where first try failed, retry passed, and no crash
        query = f"""
        SELECT remote, executor, test, branch, branch_date
            FROM results, jsonb_to_recordset(json_normal::jsonb->'results') as
                x(test text, result text, retry text, crashes text)
            WHERE x.result = 'fail'
                AND x.retry = 'pass'
                AND x.crashes IS NULL
                {branches}
            ORDER BY branch_date DESC LIMIT {limit};
        """

        cur.execute(query)
        rows = cur.fetchall()

    print(f"Query for flaky tests took: {str(datetime.datetime.now() - t)}")

    weeks_ago = []
    for weeks in range(1, 5):
        target_date = datetime.datetime.now() - datetime.timedelta(weeks=weeks)
        weeks_ago.append(target_date.strftime("%Y-%m-%d--%H-%M"))

    cnt = 0
    res = {}
    for row in rows:
        rem, exe, test, branch, br_date = row
        key = (rem, exe, test)
        if not month:
            res[key] = res.get(key, 0) + 1
        else:
            if key not in res:
                res[key] = [0, 0, 0, 0]

            for i in range(len(weeks_ago)):
                if br_date >= weeks_ago[i]:
                    res[key][i] += 1
                    break
            else:
                break  # stop looking at rows, the records are sorted by date
        cnt += 1
    # JSON needs a simple array, not a dict
    data = []
    for k, v in res.items():
        data.append({"remote": k[0], "executor": k[1], "test": k[2], "count": v})

    if month:
        # Overcount by 30 to account for fluctuation in flakiness
        flake_cnt = cnt + 30
    return data
