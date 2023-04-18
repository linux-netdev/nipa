#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2019 Netronome Systems, Inc.

import configparser
import datetime
import os
import signal
import time

import dkim
import email
import email.utils

from email.policy import default

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from core import Tree
from pw import Patchwork


should_stop = False


authorized_users = set()


pw_act_map = {
    'rejected': 'rejected',
    'reject': 'rejected',

    'changes-requested': 'changes-requested',
    'cr': 'changes-requested',

    'deferred': 'deferred',
    'defer': 'deferred'
}


def handler(signum, _):
    global should_stop

    print('Signal handler called with signal', signum)
    should_stop = True


def do_mail(msg_path, pw):
    with open(msg_path, 'rb') as fp:
        raw = fp.read()
        msg = email.message_from_bytes(raw, policy=default)
        msg_dkim = dkim.DKIM(raw)

    print('Message-ID:', msg.get('Message-ID'))
    print('', 'Subject:', msg.get('Subject'))
    print('', 'From:', msg.get('From'))

    auth = msg.get('From') in authorized_users
    if not auth:
        print('', '', 'INFO: not an authorized user, skip')
        return
    print('', 'Authorized:', auth)

    verified = msg_dkim.verify()
    print('', "DKIM verify:", verified)

    if not verified:
        print('', 'ERROR: authorized user verification failure')
        return

    actions = []
    pw_act = []
    lines = msg.get_body(preferencelist=('plain', )).as_string().split('\n')
    for line in lines:
        if line.startswith('pw-bot:'):
            actions.append(line)
            pw_act.append(line[7:].strip())
        elif line.startswith('process-bot:'):
            actions.append(line)

    if actions:
        print("Actions:")
        print('', '\n '.join(actions))
    else:
        print('INFO: authorized user but no action')
        return

    subject = msg.get('Subject')
    if subject.find(' 0/') != -1:
        obj_type = 'covers'
    else:
        obj_type = 'patches'

    mids = msg.get('References', "").split()

    series_id = 0
    for mid in mids:
        print('', '', 'PW search', mid)
        pw_obj = pw.get_by_msgid(obj_type, mid[1:-1])  # Strip the < > from mid
        if pw_obj:
            series_id = pw_obj[0]['series'][0]['id']
            print('', 'Series-id:', series_id)
            print('', '', 'Based on msg-id:', mid)
            break

    if not series_id:
        print('', 'ERROR: could not find patchwork series')
        return

    series_json = pw.get('series', series_id)
    patches = [p['id'] for p in series_json['patches']]
    if not len(patches):
        print('', 'ERROR: no patches found')
        return

    for act in pw_act:
        if act in pw_act_map:
            for pid in patches:
                pw.update_state(patch=pid, state=pw_act_map[act])
                print('', '', "INFO: Updated patch", pid, 'to', f"'{pw_act_map[act]}'")
        else:
            print('', '', "ERROR: action not in the map:", f"'{act}'")


def check_new(tree, pw):
    tree.git_fetch(tree.remote)
    hashes = tree.git(['log', "--format=%h", f'..{tree.remote}/{tree.branch}', '--reverse'])
    hashes = hashes.split()
    for h in hashes:
        tree.git(['checkout', h])
        do_mail(os.path.join(tree.path, 'm'), pw)


def main():
    # Init state
    config = configparser.ConfigParser()
    config.read(['nipa.config', 'pw.config', 'mailbot.config'])

    log_init(config.get('log', 'type', fallback='org'),
             config.get('log', 'file', fallback=os.path.join(NIPA_DIR, "mailbot.org")),
             force_single_thread=True)

    pw = Patchwork(config)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)

    global authorized_users
    users = config.get('mailbot', 'authorized')
    authorized_users.update(set(users.split(',')))

    tree_dir = config.get('dirs', 'trees', fallback=os.path.join(NIPA_DIR, "../"))
    mail_repos = {}
    for tree in config['mail-repos']:
        opts = [x.strip() for x in config['mail-repos'][tree].split(',')]
        prefix = opts[0]
        fspath = opts[1]
        remote = opts[2]
        branch = None
        if len(opts) > 3:
            branch = opts[3]
        src = os.path.join(tree_dir, fspath)
        # name, pfx, fspath, remote=None, branch=None
        mail_repos[tree] = Tree(tree, prefix, src, remote=remote, branch=branch)

    global should_stop
    while not should_stop:
        req_time = datetime.datetime.utcnow()

        for t in mail_repos.values():
            check_new(t, pw)

        secs = 120 - (datetime.datetime.utcnow() - req_time).total_seconds()
        if secs > 0:
            log("Sleep", secs)
            time.sleep(secs)


if __name__ == "__main__":
    main()
