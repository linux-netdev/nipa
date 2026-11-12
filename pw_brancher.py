#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import json
import os
import psycopg2
import subprocess
import time

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from core import Tree, Patch, PatchApplyError, PullError
from pw import Patchwork

"""
Config:

[patchwork]
check_name=contest
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
conflict_msg = "Conflicts with pending/net patches"
gate_fail_msg = "Failed gate checks"


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


def pwe_has_contest_check(pw, entry, check_name, desc, checks=None) -> bool:
    if "checks" not in entry:
        return False

    if checks is None:
        checks = pw.request_all(entry["checks"])
    last_desc = ""
    for c in checks:
        # Take the last one
        if c["context"] == check_name:
            last_desc = c.get("description", "")

    return last_desc.startswith(desc)


def pwe_post_check(pw, entry, check_name, cmt, desc, e):
    log(f"Setting 'fail' {check_name} state for: " + entry["name"])
    pw.post_check(entry["id"], name=check_name, state="fail", url="",
                  desc=f"{desc} ({cmt}):\n{e}")


def pwe_set_apply_error(pw, entry, check_name, cmt, desc, e, series_id=None):
    log_open_sec(f"Set PW '{check_name}' check state")
    if pwe_has_contest_check(pw, entry, check_name, desc):
        log(f"Skip: already has '{check_name}' check")
    elif series_id is not None:
        series_pw = pw.get("series", series_id)
        for patch in series_pw["patches"]:
            pwe_post_check(pw, patch, check_name, cmt, desc, e)
    else:
        pwe_post_check(pw, entry, check_name, cmt, desc, e)
    log_end_sec()


def pwe_has_all_checks(pw, entry, check_name) -> tuple[list[str], bool, bool]:
    if "checks" not in entry:
        return [], False, False
    checks = pw.request_all(entry["checks"])
    found = {}
    for c in checks:
        if c["context"] in gate_checks:
            found[c["context"]] = int(c["state"] == "success")

    # Note: the gate checks are currently only reporting success or failure
    failed = [name for name, value in found.items() if value != 1]
    present = len(found) == len(gate_checks)
    has_contest = (bool(failed) and
                   pwe_has_contest_check(pw, entry, check_name, gate_fail_msg,
                                         checks))

    return failed, present, has_contest


def pwe_series_id_or_none(entry) -> int:
    if len(entry.get("series", [])) > 0:
        return entry["series"][0]["id"]


def pwe_get_pending(pw, check_name) -> list:
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
    skip_check_fail_series = {}
    skip_check_missing_series = set()
    reported_fail_series = set()
    res = []
    for entry in things:
        series_id = pwe_series_id_or_none(entry)
        if series_id in skip_check_fail_series:
            log("Skip because of failing check elsewhere in the series: " + entry["name"])
            if series_id not in reported_fail_series:
                cmt = "elsewhere in the series"
                e = skip_check_fail_series[series_id]
                pwe_post_check(pw, entry, check_name, cmt, gate_fail_msg, e)
            continue
        elif series_id in skip_check_missing_series:
            log("Skip because of missing check elsewhere in the series: " + entry["name"])
            continue

        failed, present, has_contest = pwe_has_all_checks(pw, entry, check_name)
        if failed:
            log("Skip because of failing check: " + entry["name"])
            if has_contest:
                log("Already reported on PW: " + entry["name"])
                if series_id:
                    reported_fail_series.add(series_id)
            else:
                e = "blocked because of failures: " + ", ".join(failed)
                pwe_post_check(pw, entry, check_name, "here", gate_fail_msg, e)
            if series_id:
                skip_check_fail_series[series_id] = f"patch id: {entry['id']}"
        elif not present:
            log("Skip because of missing check: " + entry["name"])
            if series_id:
                skip_check_missing_series.add(series_id)
        else:
            res.append(entry)
    things = res
    log_end_sec()

    log_open_sec("Filter by checks by other patches in the series")
    res = []
    for entry in things:
        series_id = pwe_series_id_or_none(entry)
        if series_id in skip_check_fail_series:
            log("Skip because of failing check elsewhere in the series: " + entry["name"])
            if series_id not in reported_fail_series:
                cmt = "elsewhere in the series"
                e = skip_check_fail_series[series_id]
                pwe_post_check(pw, entry, check_name, cmt, gate_fail_msg, e)
        elif series_id in skip_check_missing_series:
            log("Skip because of missing check elsewhere in the series: " + entry["name"])
        else:
            res.append(entry)
    things = res
    log_end_sec()

    return things


def apply_pending_patches(pw, config, tree, branch_name) -> tuple[list, list]:
    log_open_sec("Get pending submissions from patchwork")
    check_name = config.get('patchwork', 'check_name', fallback='contest')
    things = pwe_get_pending(pw, check_name)
    log(f"Have {len(things)} pending things from patchwork")
    log_end_sec()

    log_open_sec("Applying pending submissions")
    seen_series = set()
    applied_series = set()
    applied_prs = set()
    desc = conflict_msg
    for entry in things:
        series_id = pwe_series_id_or_none(entry)
        if series_id in seen_series:
            continue

        if entry.get('pull_url', None):
            log_open_sec("Pulling: " + entry["name"])
            try:
                tree.pull(entry["pull_url"], reset=False)
                applied_prs.add(entry["id"])
            except PullError as e:
                pwe_set_apply_error(pw, entry, check_name, branch_name, desc, e)
        else:
            log_open_sec("Applying: " + entry["series"][0]["name"])
            seen_series.add(series_id)
            mbox_url = entry["series"][0]["mbox"]
            data = pw.get_mbox_direct(mbox_url)
            p = Patch(data)
            try:
                tree.apply(p)
                applied_series.add(series_id)
            except PatchApplyError as e:
                pwe_set_apply_error(pw, entry, check_name, branch_name, desc, e,
                                    series_id)
        log_end_sec()
    log_end_sec()

    return list(applied_series), list(applied_prs)


def _apply_local_patch(path, tree, dir_path=None) -> bool:
    """Apply a single patch file to the tree."""
    log_msg = "Applying: " + path
    if dir_path:
        log_msg += f" (dir: {dir_path})"
    log_open_sec(log_msg)
    try:
        with open(path, "r") as fp:
            data = fp.read()
        p = Patch(data)
        try:
            tree.apply(p)
            success = True
        except PatchApplyError:
            success = False
    except Exception as e:
        log(f"Error reading or applying patch {path}: {str(e)}")
        success = False
    log_end_sec()
    return success

def apply_local_patches(config, tree) -> list:
    extras = []
    for entry in config.get("local", "patches", fallback="").split(','):
        if not entry:
            continue

        if os.path.isdir(entry):
            # If entry is a directory, apply all .patch files in it
            for filename in os.listdir(entry):
                if filename.endswith(".patch"):
                    patch_path = os.path.join(entry, filename)
                    if _apply_local_patch(patch_path, tree, entry):
                        extras.append(patch_path)
        else:
            # Regular file handling
            if _apply_local_patch(entry, tree):
                extras.append(entry)

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
                   cwd=tree.path, check=False)


def get_change_from_last(tree, branch_list) -> bool:
    branch_list = sorted(branch_list)
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
                tree.pull(url, reset=False, ff=False)
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

    series, prs = apply_pending_patches(pw, config, tree, branch_name)
    state["info"][branch_name] |= {"series": series, "prs": prs}

    extras = apply_local_patches(config, tree)
    state["info"][branch_name]["extras"] = extras

    state["info"][branch_name]["new-changes"] = get_change_from_last(tree, state["info"].keys())

    state["branches"][branch_name] = now.isoformat()

    db_insert(config, state, branch_name)

    log_open_sec("Pushing out")
    tree.git_push(tgt_remote, "HEAD:refs/heads/" + branch_name)
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
    write_json_atomic(config.get("output", "info"), state["info"])
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
