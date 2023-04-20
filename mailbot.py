#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2019 Netronome Systems, Inc.

import configparser
import datetime
import os
import requests
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
auto_changes_requested = set()


pw_act_map = {
    'au': 'awaiting-upstream',
    'awaiting-upstream': 'awaiting-upstream',

    'rejected': 'rejected',
    'reject': 'rejected',

    'changes-requested': 'changes-requested',
    'cr': 'changes-requested',

    'deferred': 'deferred',
    'defer': 'deferred',

    'not-applicable': 'not-applicable',
    'nap': 'not-applicable',

    'needs-ack': 'needs-ack',
    'need-ack': 'needs-ack',
    'nac': 'needs-ack',

    'under-review': 'under-review',
    'ur': 'under-review'
}

#
# DocRef code
#


class DocTooManyMatches(Exception):
    pass


class DocNotFound(Exception):
    pass


class DocReference:
    def __init__(self, tag):
        self.tag = tag
        self.title = tag
        self.lines = []

    def set_title(self, title):
        if self.title != self.tag:
            raise Exception(f'Title for {self.tag} already set to "{self.title}" now "{title}"')
        self.title = title

    def add_line(self, line):
        self.lines.append(line)

    def __repr__(self):
        ret = self.title + '\n'
        ret += '\n'.join(self.lines)
        return ret


class DocRefs:
    def __init__(self):
        self.refs = dict()
        self.loc_map = dict()
        self.name_alias = dict()

    def _unalias_name(self, name):
        if name in self.name_alias:
            return self.name_alias[name]
        return name

    def search(self, name, tag):
        """
        Find the relevant doc based on inputs. The name is optional but if it is
        specified it must much exactly. Tag may match partially, full matches take
        precedence. If multiple equivalent matches are found error will be returned.

        :param name: exact match for doc, optional
        :param tag: partial or exact match on section
        :return: tuple of (doc, tag) which can be used to get text out of get_doc()
        """
        name = self._unalias_name(name)

        match = None
        match_n = None
        full_match = False

        for n in self.refs:
            # If name is empty search all, otherwise only the matching section
            if name and name != n:
                continue
            for t in self.refs[n]:
                if tag in t:
                    is_full = (t == tag) and (not name or n == name)
                    if match and (full_match == is_full):
                        raise DocTooManyMatches(f'{name}/{tag} matched both {match_n}/{match} and {n}/{t}')
                    if is_full >= full_match:
                        full_match = is_full
                        match = t
                        match_n = n
        if not match:
            raise DocNotFound(f'{name}/{tag} not found')

        return match_n, match

    def get_doc(self, name, tag):
        name = self._unalias_name(name)

        ret = repr(self.refs[name][tag])
        ret += '\n\n'
        ret += f'See: https://www.kernel.org/doc/html/next/{self.loc_map[name]}.html#{tag}'
        return ret

    def alias_section(self, name, alias):
        self.name_alias[alias] = name

    def _sphinx_title_to_heading(self, name):
        heading = []
        for i in range(len(name)):
            # Leading numbers are definitely removed, not sure about mid-title numbers
            if name[i].isalpha():
                heading.append(name[i].lower())
            elif len(heading) == 0:
                pass
            elif heading[-1] != "-":
                heading.append("-")
        if len(heading) and heading[-1] == "-":
            heading.pop()

        return "".join(heading)

    def load_section(self, location, name):
        self.refs[name] = dict()
        refs = self.refs[name]

        self.loc_map[name] = location

        r = requests.get(f'https://www.kernel.org/doc/html/next/{location}.html')
        data = r.content.decode('utf-8')

        offs = 0
        while True:
            # Find all the sections in the HTML version of the doc
            offs = data.find('<section id=', offs)
            if offs == -1:
                break
            offs += 13  # skip '<section id="'
            start = offs
            end = start + 1
            while data[end] != '"' and len(data) > end:
                end += 1
            refs[data[start:end]] = DocReference(data[start:end])
            offs += 1

        # Now populate the plain text contents
        url = f'https://git.kernel.org/pub/scm/linux/kernel/git/next/linux-next.git/plain/Documentation/{location}.rst'
        r = requests.get(url)
        data = r.content.decode('utf-8')
        lines = data.split('\n')

        headings = {'-', '~', '='}
        docref = DocReference('')  # Make a fake one so we don't have to None-check
        fakeref = docref
        prev = ""
        for l in lines:
            # Non-headings get fed into the current section
            if len(l) == 0 or l[0] not in headings:
                docref.add_line(prev)
                prev = l
                continue
            if l != l[0] * len(l):
                docref.add_line(prev)
                prev = l
                continue

            # Headings are kept as 'docref'
            heading = self._sphinx_title_to_heading(prev)
            if heading:
                if heading not in refs:
                    print('Unknown heading', heading)
                    docref = fakeref
                else:
                    docref = refs[heading]
                    docref.set_title(prev)
            prev = l


#
# Email
#

class MlEmail:
    def __init__(self, msg_path):
        self.msg = None

        with open(msg_path, 'rb') as fp:
            raw = fp.read()
            self.msg = email.message_from_bytes(raw, policy=default)
            self._dkim = dkim.DKIM(raw)

        self.actions = []
        self.pw_act = []
        self.dr_act = []

        # Lazy eval because its slow
        self._dkim_ok = None

    def get(self, item, failobj=None):
        return self.msg.get(item, failobj)

    def user_authorized(self):
        return self.msg.get('From') in authorized_users

    def user_bot(self):
        return self.msg.get('From') in auto_changes_requested

    def dkim_ok(self):
        if self._dkim_ok is None:
            self._dkim_ok = self._dkim.verify()
        return self._dkim_ok

    def extract_actions(self):
        """
        Extract actions and load them into the action lists.

        Lazy exec because we don't want to parse unauthorized emails
        """
        if self.user_authorized() and self.dkim_ok():
            lines = self.msg.get_body(preferencelist=('plain',)).as_string().split('\n')
            for line in lines:
                if line.startswith('pw-bot:'):
                    self.actions.append(line)
                    self.pw_act.append(line[7:].strip())
                elif line.startswith('doc-bot:'):
                    self.actions.append(line)
                    self.dr_act.append(line[8:].strip())
        elif self.user_bot():
            self.pw_act.append('changes-requested')

#
# Unsorted, rest of the bot and pw handling
#


def handler(signum, _):
    global should_stop

    print('Signal handler called with signal', signum)
    should_stop = True


def do_mail(msg_path, pw, dr):
    msg = MlEmail(msg_path)

    print('Message-ID:', msg.get('Message-ID'))
    print('', 'Subject:', msg.get('Subject'))
    print('', 'From:', msg.get('From'))

    if not msg.user_authorized() and not msg.user_bot():
        print('', '', 'INFO: not an authorized user, skip')
        return
    print('', 'Authorized:', msg.user_authorized())
    print('', "DKIM verify:", msg.dkim_ok())

    if not msg.dkim_ok():
        print('', 'ERROR: authorized user verification failure')
        return

    msg.extract_actions()
    if msg.actions:
        print("Actions:")
        print('', '\n '.join(msg.actions))
    else:
        print('INFO: authorized user but no action')
        return

    subject = msg.get('Subject')
    if subject.find(' 0/') != -1 or subject.find(' 00/') != -1:
        obj_type = 'covers'
    else:
        obj_type = 'patches'

    mids = msg.get('References', "").split()

    series_id = 0
    for mid in mids:
        print('', '', 'PW search', obj_type, mid)
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

    for act in msg.pw_act:
        if act in pw_act_map:
            for pid in patches:
                pw.update_state(patch=pid, state=pw_act_map[act])
                print('', '', "INFO: Updated patch", pid, 'to', f"'{pw_act_map[act]}'")
        else:
            print('', '', "ERROR: action not in the map:", f"'{act}'")

    for act in msg.dr_act:
        names = act.split('/')
        if len(names) > 2 or len(names) < 1:
            print('', '', "ERROR: bad doc action token count:", act)
            continue
        if len(names) == 1:
            names = [''] + names

        try:
            name, sec = dr.search(names[0], names[1])
            print('', '', 'INFO: have doc for', act, 'exact coordinates', f"{name}/{sec}")
        except:
            print('', '', "ERROR: failed doc search:", act)

        if act in pw_act_map:
            for pid in patches:
                pw.update_state(patch=pid, state=pw_act_map[act])
                print('', '', "INFO: Updated patch", pid, 'to', f"'{pw_act_map[act]}'")
        else:
            print('', '', "ERROR: action not in the map:", f"'{act}'")


def check_new(tree, pw, dr):
    tree.git_fetch(tree.remote)
    hashes = tree.git(['log', "--format=%h", f'..{tree.remote}/{tree.branch}', '--reverse'])
    hashes = hashes.split()
    for h in hashes:
        tree.git(['checkout', h])
        do_mail(os.path.join(tree.path, 'm'), pw, dr)


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

    global auto_changes_requested
    users = config.get('mailbot', 'error-bots')
    auto_changes_requested.update(set(users.split(',')))

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

    doc_load_time = datetime.datetime.fromtimestamp(0)
    dr = None

    global should_stop
    while not should_stop:
        req_time = datetime.datetime.utcnow()

        if (req_time - doc_load_time).total_seconds() > 24 * 60 * 60:
            dr = DocRefs()
            dr.load_section('process/maintainer-netdev', 'net')
            dr.alias_section('net', 'netdev')
            dr.load_section('process/coding-style', 'coding')
            dr.alias_section('coding', 'code')
            dr.load_section('process/submitting-patches', 'submitting-patches')
            dr.alias_section('submitting-patches', 'submit')
            dr.alias_section('submitting-patches', 'sub')

        for t in mail_repos.values():
            check_new(t, pw, dr)

        secs = 120 - (datetime.datetime.utcnow() - req_time).total_seconds()
        while secs > 0:
            time.sleep(3)
            secs -= 3


if __name__ == "__main__":
    main()
