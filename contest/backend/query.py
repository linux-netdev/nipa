#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0


from flask import Flask
from flask import request
import couchdb
import os
import datetime


app = Flask("NIPA contest query")

user = os.getenv('DB_USER')
pwd = os.getenv('DB_PWD')
couch = couchdb.Server(f'http://{user}:{pwd}@127.0.0.1:5984')
res_db = couch["results"]


def branches_to_rows(br_cnt):
    data = res_db.view('branch/rows', None,
                       group=True, descending=True, limit=br_cnt)
    cnt = 0
    for row in data:
        cnt += row.value
    return cnt


@app.route('/')
def hello():
    return '<h1>boo!</h1>'


@app.route('/results')
def results():
    global couch

    t1 = datetime.datetime.now()

    br_name = request.args.get('branch-name')
    if br_name:
        t1 = datetime.datetime.now()
        rows = [r.value for r in res_db.view('branch/row_fetch', None,
                                             key=br_name, limit=100)]
        t2 = datetime.datetime.now()
        print("Query for exact branch took: ", str(t2-t1))
        return rows

    br_cnt = request.args.get('branches')
    try:
        br_cnt = int(br_cnt)
    except:
        br_cnt = None
    if not br_cnt:
        br_cnt = 10

    need_rows = branches_to_rows(br_cnt)
    t2 = datetime.datetime.now()
    data = [r.value for r in res_db.view('branch/row_fetch', None,
                                         descending=True, limit=need_rows)]

    t3 = datetime.datetime.now()
    print(f"Query for {br_cnt} branches, {need_rows} records took: {str(t3-t1)} ({str(t2-t1)}+{str(t3-t2)})")

    return data
