#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import json
import os
import time

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from core import Tree

"""
Config:

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
"""


def hour_timestamp(when=None) -> int:
    if when is None:
        when = datetime.datetime.now(datetime.UTC)
    ts = when.timestamp()
    return int(ts / (60 * 60))


def create_new(config, state, tree, tgt_remote) -> None:
    now = datetime.datetime.now(datetime.UTC)
    pfx = config.get("target", "branch_pfx")
    branch_name = pfx + datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d--%H-%M")

    log_open_sec("Fetching latest net-next")
    tree.git_fetch(tree.remote)
    tree.git_reset(tree.branch, hard=True)
    log_end_sec()

    pull_list = config.get("target", "pull", fallback=None)
    if pull_list:
        log_open_sec("Pulling in other trees")
        for url in pull_list.split(','):
            tree.git_pull(url)
        log_end_sec()

    state["branches"][branch_name] = now.isoformat()

    log_open_sec("Pushing out")
    tree.git_push(tgt_remote, "HEAD:" + branch_name)
    log_end_sec()


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
        br = br[len(r_tgt_pfx):]
        found.add(br)
        if br not in state["branches"]:
            tree.git_push(tgt_remote, ':' + br)
            continue
        when = datetime.datetime.fromisoformat(state["branches"][br])
        if now - when > datetime.timedelta(days=5):
            tree.git_push(tgt_remote, ':' + br)
            del state["branches"][br]
            continue
    state_has = set(state["branches"].keys())
    lost = state_has.difference(found)
    for br in lost:
        log_open_sec("Removing lost branch " + br + " from state")
        del state["branches"][br]
        log_end_sec()
    log_end_sec()


def dump_branches(config, state) -> None:
    log_open_sec("Update branches manifest")
    pub_url = config.get('target', 'public_url')

    data = []
    for name, val in state["branches"].items():
        data.append({"branch": name, "date": val, "url": pub_url + " " + name})

    branches = config.get("output", "branches")
    with open(branches, 'w') as fp:
        json.dump(data, fp)
    log_end_sec()


def main_loop(config, state, tree, tgt_remote) -> None:
    now = datetime.datetime.now(datetime.UTC)
    now_h = hour_timestamp(now)
    freq = int(config.get("target", "freq"))
    if now_h - state["last"] < freq or now_h % freq != 0:
        time.sleep(20)
        return

    reap_old(config, state, tree, tgt_remote)
    create_new(config, state, tree, tgt_remote)

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


def main() -> None:
    config = configparser.ConfigParser()
    config.read(['nipa.config', 'pw.config', 'brancher.config'])

    log_init(config.get('log', 'type', fallback='stdout'),
             config.get('log', 'file', fallback=None))

    state = {}
    if os.path.exists("brancher.state"):
        with open("brancher.state") as fp:
            state = json.load(fp)

    if "last" not in state:
        state["last"] = 0
    if "branches" not in state:
        state["branches"] = {}

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
            main_loop(config, state, tree, tgt_remote)
    finally:
        with open('brancher.state', 'w') as f:
            json.dump(state, f)


if __name__ == "__main__":
    main()
