#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import json
import os
import psycopg2
import subprocess
import time
from typing import List, Tuple
import uuid

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from core import Tree, Patch, PatchApplyError, PullError
from pw import Patchwork

"""
Config:

[filters]
ignore_delegate=bpf
gate_checks=build_clang,build_32bit,build_allmodconfig_warn
[trees]
net-next=net-next, net-next, origin, origin/main
[target]
public_url=https://github.com/linux-netdev/testing.git
push_url=git@github.com:linux-netdev/testing.git
branch_pfx=net-next-
freq=3
pull=git://git.kernel.org/pub/scm/linux/kernel/git/netdev/net.git
[output]
branches=branches.json
info=branches-info.json
deltas=/path/to/dir/
[db]
db=db-name
"""


psql_conn = None
ignore_delegate = {}
gate_checks = {}


def write_json_atomic(path, data):
    tmp = path + '.new'
    with open(tmp, 'w') as fp:
        json.dump(data, fp)
    os.rename(tmp, path)


def hour_timestamp(when=None) -> int:
    if when is None:
        when = datetime.datetime.now(datetime.UTC)
    ts = when.timestamp()
    return int(ts / (60 * 60))


def pwe_has_all_checks(pw, entry) -> bool:
    if "checks" not in entry:
        return False
    checks = pw.request(entry["checks"])
    found = dict.fromkeys(gate_checks, 0)
    for c in checks:
        if c["context"] in gate_checks:
            found[c["context"]] = int(c["state"] == "success")
    return sum(found.values()) == len(gate_checks)


def pwe_series_id_or_none(entry) -> int:
    if len(entry.get("series", [])) > 0:
        return entry["series"][0]["id"]


def pwe_get_pending(pw, config) -> List:
    log_open_sec("Loading patches")
    things = pw.get_patches_all(action_required=True)
    log_end_sec()

    log_open_sec("Filter by delegates")
    res = []
    for entry in things:
        delegate = entry.get("delegate", None)
        if delegate and delegate["username"] in ignore_delegate:
            log(f"Skip because of delegate ({delegate['username']}): " + entry["name"])
        else:
            res.append(entry)
    things = res
    log_end_sec()

    log_open_sec("Filter by checks")
    skip_check_series = set()
    res = []
    for entry in things:
        series_id = pwe_series_id_or_none(entry)
        if series_id in skip_check_series:
            log("Skip because of failing/missing check elsewhere in the series: " + entry["name"])
        elif not pwe_has_all_checks(pw, entry):
            log("Skip because of failing/missing check: " + entry["name"])
            if series_id:
                skip_check_series.add(series_id)
        else:
            res.append(entry)
    things = res
    log_end_sec()

    log_open_sec("Filter by checks by other patches in the series")
    res = []
    for entry in things:
        if pwe_series_id_or_none(entry) in skip_check_series:
            log("Skip because of failing check elsewhere in the series: " + entry["name"])
        else:
            res.append(entry)
    things = res
    log_end_sec()

    return things


def apply_pending_patches(pw, config, tree) -> Tuple[List, List]:
    log_open_sec("Get pending submissions from patchwork")
    things = pwe_get_pending(pw, config)
    log(f"Have {len(things)} pending things from patchwork")
    log_end_sec()

    log_open_sec("Applying pending submissions")
    seen_series = set()
    applied_series = set()
    applied_prs = set()
    for entry in things:
        series_id = pwe_series_id_or_none(entry)
        if series_id in seen_series:
            continue

        if entry.get('pull_url', None):
            log_open_sec("Pulling: " + entry["name"])
            try:
                tree.pull(entry["pull_url"], reset=False)
                applied_prs.add(entry["id"])
            except PullError:
                pass
        else:
            log_open_sec("Applying: " + entry["series"][0]["name"])
            seen_series.add(series_id)
            mbox_url = entry["series"][0]["mbox"]
            data = pw.get_mbox_direct(mbox_url)
            p = Patch(data)
            try:
                tree.apply(p)
                applied_series.add(series_id)
            except PatchApplyError:
                pass
        log_end_sec()
    log_end_sec()

    return list(applied_series), list(applied_prs)


def apply_local_patches(config, tree) -> List:
    extras = []
    for entry in config.get("local", "patches", fallback="").split(','):
        with open(entry, "r") as fp:
            data = fp.read()

        log_open_sec("Applying: " + entry)
        p = Patch(data)
        try:
            tree.apply(p)
            extras.append(entry)
        except PatchApplyError:
            pass
        log_end_sec()

    return extras


def db_insert(config, state, name):
    # Branches usually have a trailing separator
    pfx = config.get("target", "branch_pfx")[:-1]
    pub_url = config.get('target', 'public_url')
    row = {"branch": name,
           "date": state["branches"][name],
           "base": state["hashes"].get(name, None),
           "url": pub_url + " " + name}
    row |= state["info"][name]

    with psql_conn.cursor() as cur:
        cols = "(branch, stream, t_date, base, url, info)"
        arg = cur.mogrify("(%s,%s,%s,%s,%s,%s)",
                           (row["branch"], pfx, row["date"], row["base"],
                            row["url"], json.dumps(row)))
        cur.execute(f"INSERT INTO branches {cols} VALUES " + arg.decode('utf-8'))


def generate_deltas(config, tree, name):
    outdir = config.get("output", "deltas", fallback=None)
    if not outdir:
        return

    outfile = os.path.join(outdir, name)
    cidiff = os.path.join(os.path.dirname(__file__), "contest", "cidiff")

    with open(outfile, 'w') as fp:
        subprocess.run([cidiff, name], cwd=tree.path, stdout=fp, check=True)

    outfile += ".html"
    cidiff = os.path.join(os.path.dirname(__file__), "contest", "cidiff.py")
    # pub_url is for git, so it most likely ends with ".git"
    pub_url = config.get('target', 'public_url')[:-4]
    subprocess.run([cidiff, name, '-H', '-o', outfile, '-g', pub_url],
                   cwd=tree.path, check=True)


def get_change_from_last(tree, branch_list) -> bool:
    branch_list = list(sorted(branch_list))
    if len(branch_list) < 2:
        return True

    try:
        tree.git(['diff', '--quiet', branch_list[-1], branch_list[-2]])
        return False
    except:
        return True


def create_new(pw, config, state, tree, tgt_remote) -> None:
    now = datetime.datetime.now(datetime.UTC)
    pfx = config.get("target", "branch_pfx")
    branch_name = pfx + datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d--%H-%M")

    log_open_sec("Fetching latest net-next")
    tree.git_fetch(tree.remote)
    tree.git_reset(tree.branch, hard=True)
    log_end_sec()

    state["info"][branch_name] = {"base-pulls":{}}

    pull_list = config.get("target", "pull", fallback=None)
    if pull_list:
        log_open_sec("Pulling in other trees")
        for url in pull_list.split(','):
            try:
                tree.pull(url, reset=False)
                state["info"][branch_name]["base-pulls"][url] = "okay"
            except PullError:
                try:
                    tree.pull(url, reset=False, trust_rerere=True)
                    state["info"][branch_name]["base-pulls"][url] = "resolved"
                except PullError:
                    log("PULL FAILED")
                    state["info"][branch_name]["base-pulls"][url] = "fail"

        log_end_sec()

    state["hashes"][branch_name] = tree.head_hash()

    series, prs = apply_pending_patches(pw, config, tree)
    state["info"][branch_name] |= {"series": series, "prs": prs}

    extras = apply_local_patches(config, tree)
    state["info"][branch_name]["extras"] = extras

    state["info"][branch_name]["new-changes"] = get_change_from_last(tree, state["info"].keys())

    state["branches"][branch_name] = now.isoformat()

    db_insert(config, state, branch_name)

    log_open_sec("Pushing out")
    tree.git_push(tgt_remote, "HEAD:" + branch_name)
    log_end_sec()

    log_open_sec("Generate deltas")
    generate_deltas(config, tree, branch_name)
    log_end_sec()


def state_delete_branch(state, br):
    del state["branches"][br]
    state["hashes"].pop(br, None)
    state["info"].pop(br, None)


def reap_old(config, state, tree, tgt_remote) -> None:
    now = datetime.datetime.now(datetime.UTC)
    pfx = config.get("target", "branch_pfx")

    log_open_sec("Clean up old branches")
    tree.git_fetch(tgt_remote)

    branches = tree.git(['branch', '-a'])
    branches = branches.split('\n')
    r_tgt_pfx = 'remotes/' + tgt_remote + '/'

    found = set()
    for br in branches:
        br = br.strip()
        if not br.startswith(r_tgt_pfx + pfx):
            continue
        # In case our prefix is a prefix of another brancher
        if len(br) != len(r_tgt_pfx + pfx + "2000-01-01--00-00"):
            continue
        br = br[len(r_tgt_pfx):]
        found.add(br)
        if br not in state["branches"]:
            tree.git_push(tgt_remote, ':' + br)
            continue
        when = datetime.datetime.fromisoformat(state["branches"][br])
        if now - when > datetime.timedelta(days=5):
            tree.git_push(tgt_remote, ':' + br)
            state_delete_branch(state, br)
            continue
    state_has = set(state["branches"].keys())
    lost = state_has.difference(found)
    for br in lost:
        log_open_sec("Removing lost branch " + br + " from state")
        state_delete_branch(state, br)
        log_end_sec()
    log_end_sec()


def dump_branches(config, state) -> None:
    log_open_sec("Update branches manifest")
    pub_url = config.get('target', 'public_url')

    data = []
    for name, val in state["branches"].items():
        data.append({"branch": name,
                     "date": val,
                     "base": state["hashes"].get(name, None),
                     "url": pub_url + " " + name})

    write_json_atomic(config.get("output", "branches"), data)

    info = config.get("output", "info")
    with open(info, 'w') as fp:
        json.dump(state["info"], fp)
    log_end_sec()


def main_loop(pw, config, state, tree, tgt_remote) -> None:
    now = datetime.datetime.now(datetime.UTC)
    now_h = hour_timestamp(now)
    freq = int(config.get("target", "freq"))
    if now_h - state["last"] < freq or now_h % freq != 0:
        return

    reap_old(config, state, tree, tgt_remote)
    create_new(pw, config, state, tree, tgt_remote)

    state["last"] = now_h

    dump_branches(config, state)


def prep_remote(config, tree) -> str:
    tgt_tree = config.get('target', 'push_url')

    log_open_sec("Prep remote")
    remotes = tree.remotes()
    for r in remotes:
        if remotes[r]["push"] == tgt_tree:
            log("Found remote, it is " + r)
            log_end_sec()
            return r

    log("Remote not found, adding")

    if "brancher" in remotes:
        log("Remote 'brancher' already exists with different URL")
        raise Exception("Remote exists with different URL")

    tree.git(['remote', 'add', 'brancher', tgt_tree])
    log_end_sec()

    return "brancher"


def open_db(config):
    db_name = config.get("db", "db")
    conn = psycopg2.connect(database=db_name)
    conn.autocommit = True
    return conn


def main() -> None:
    config = configparser.ConfigParser()
    config.read(['nipa.config', 'pw.config', 'brancher.config'])

    log_init(config.get('log', 'type', fallback='stdout'),
             config.get('log', 'file', fallback=None))

    pw = Patchwork(config)

    state = {}
    if os.path.exists("brancher.state"):
        with open("brancher.state") as fp:
            state = json.load(fp)

    if "last" not in state:
        state["last"] = 0
    if "branches" not in state:
        state["branches"] = {}
    if "hashes" not in state:
        state["hashes"] = {}
    if "info" not in state:
        state["info"] = {}

    # Parse global config
    global ignore_delegate
    ignore_delegate = set(config.get('filters', 'ignore_delegate', fallback="").split(','))
    global gate_checks
    gate_checks = set(config.get('filters', 'gate_checks', fallback="").split(','))
    global psql_conn
    psql_conn = open_db(config)

    tree_obj = None
    tree_dir = config.get('dirs', 'trees', fallback=os.path.join(NIPA_DIR, "../"))
    for tree in config['trees']:
        opts = [x.strip() for x in config['trees'][tree].split(',')]
        prefix = opts[0]
        fspath = opts[1]
        remote = opts[2]
        branch = opts[3]
        src = os.path.join(tree_dir, fspath)
        # name, pfx, fspath, remote=None, branch=None
        tree_obj = Tree(tree, prefix, src, remote=remote, branch=branch)
    tree = tree_obj

    tgt_remote = prep_remote(config, tree)

    try:
        while True:
            main_loop(pw, config, state, tree, tgt_remote)
            try:
                time.sleep(20)
            except KeyboardInterrupt:
                break
    finally:
        with open('brancher.state', 'w') as f:
            json.dump(state, f)


if __name__ == "__main__":
    main()
