#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
# pylint: disable=missing-module-docstring,missing-function-docstring
# pylint: disable=missing-class-docstring,wrong-import-position
# pylint: disable=import-error,global-statement,invalid-name
# pylint: disable=wrong-import-order

import argparse
import configparser
import datetime
import os
import signal
import smtplib
import subprocess
import sys
import time

from email.mime.text import MIMEText

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core import log_init
from core import Tree
from core.cmd import CmdError

from db import AgentDB
from ml_email import (
    parse_email, split_from, extract_bracket_contents,
    extract_title, extract_version, is_submission,
    is_resubmission_candidate, extract_pv_bot_tags,
)


should_stop = False
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))


def handler(signum, _):
    global should_stop
    print('Signal handler called with signal', signum)
    should_stop = True


def load_template(name):
    path = os.path.join(AGENT_DIR, 'form-letters', name)
    with open(path, encoding='utf-8') as fp:
        return fp.read()


def load_templates():
    return {
        'welcome': load_template('welcome'),
        'resubmit-warn': load_template('resubmit-warn'),
        'threaded-warn': load_template('threaded-warn'),
    }


def send_email(config, to, subject, body, dry_run=False):
    if dry_run:
        return False

    try:
        server = config.get('ml-agent-smtp', 'server')
        port = config.getint('ml-agent-smtp', 'port')
        user = config.get('ml-agent-smtp', 'user')
        password = config.get('ml-agent-smtp', 'password')
        from_addr = config.get('ml-agent-smtp', 'from')
    except (configparser.NoSectionError, configparser.NoOptionError):
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to

    try:
        with smtplib.SMTP_SSL(server, port, timeout=30) as srv:
            srv.login(user, password)
            srv.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        print(f'  WARNING: failed to send email: {e}')
        return False


def check_known_developer(config, db, identity_id):
    known_dev, _ = db.get_identity(identity_id)
    if known_dev != 0:
        return known_dev

    try:
        linux_tree = config.get('ml-agent', 'linux-tree')
    except (configparser.NoSectionError, configparser.NoOptionError):
        return 0

    emails = db.get_identity_emails(identity_id)
    names = db.get_identity_names(identity_id)

    all_dates = set()
    for query in emails + names:
        try:
            out = subprocess.run(
                ['git', 'log', '--all', '--since=2.years.ago',
                 '--format=%H %ad', '--date=short',
                 f'--author={query}'],
                cwd=linux_tree, capture_output=True, text=True, timeout=60,
                check=False)
            for line in out.stdout.strip().split('\n'):
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    all_dates.add(parts[1])
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f'  WARNING: git query failed for {query}: {e}')

    count = len(all_dates)
    if count == 0:
        db.set_known_dev(identity_id, 2)
        return 2

    six_months_ago = (datetime.datetime.now() -
                      datetime.timedelta(days=180)).strftime('%Y-%m-%d')
    all_recent = all(d >= six_months_ago for d in all_dates)

    if count < 5 and all_recent:
        db.set_known_dev(identity_id, 2)
        return 2

    db.set_known_dev(identity_id, 1)
    return 1


def process_email(msg, message_id, timestamp, config, db, templates,
                  dry_run=False, decisions=None):
    from_hdr = msg.get('From', '')
    subject = msg.get('Subject', '')
    name, email_addr = split_from(from_hdr)
    if not email_addr:
        return

    identity_id = db.resolve_identity(name, email_addr)

    pv_tags = extract_pv_bot_tags(msg)
    if pv_tags:
        to_hdr = msg.get('To', '')
        to_name, to_email = split_from(to_hdr)
        if to_email:
            to_identity = db.resolve_identity(to_name, to_email)
            for tag in pv_tags:
                db.add_pv_bot_action(message_id, to_identity, tag, timestamp)

    if is_submission(msg):
        bracket = extract_bracket_contents(subject)
        title = extract_title(subject)
        version = extract_version(bracket)

        db.add_submission(message_id, identity_id, title, version, timestamp)

        dup = db.find_recent_duplicate(identity_id, title, timestamp)
        if dup:
            db.set_submission_warned(message_id, 1)
            send_email(config, from_hdr, f'Re: {subject}',
                       templates['resubmit-warn'], dry_run)
            if decisions is not None:
                decisions.append(('resubmit-warn', email_addr, title))

        known = check_known_developer(config, db, identity_id)
        if known == 2:
            _, welcomed = db.get_identity(identity_id)
            if not welcomed:
                db.set_welcomed(identity_id)
                send_email(config, from_hdr, f'Re: {subject}',
                           templates['welcome'], dry_run)
                if decisions is not None:
                    decisions.append(('welcome', email_addr, title))
            else:
                if decisions is not None:
                    decisions.append(('skip-welcome-already', email_addr,
                                      title))
        elif known == 1 and decisions is not None:
            decisions.append(('skip-welcome-known', email_addr, title))

    elif is_resubmission_candidate(msg):
        bracket = extract_bracket_contents(subject)
        title = extract_title(subject)
        version = extract_version(bracket)

        prev = db.find_previous_version(identity_id, title, version)
        if prev:
            db.set_submission_warned(prev[0], 2)
            send_email(config, from_hdr, f'Re: {subject}',
                       templates['threaded-warn'], dry_run)
            if decisions is not None:
                decisions.append(('threaded-warn', email_addr, title))
        elif decisions is not None:
            decisions.append(('skip-threaded-noprev', email_addr, title))


def fetch_tree(tree):
    for _ in range(3):
        try:
            tree.git_fetch(tree.remote)
            return
        except CmdError:
            print('WARNING: git fetch failed, retrying')
            time.sleep(300)


def check_new(tree, config, db, templates, dry_run=False):
    fetch_tree(tree)
    hashes = tree.git(
        ['log', "--format=%h", f'..{tree.remote}/{tree.branch}', '--reverse'])
    hashes = hashes.split()
    for h in hashes:
        tree.git(['checkout', h])
        msg_path = os.path.join(tree.path, 'm')
        if not os.path.exists(msg_path):
            continue

        msg = parse_email(msg_path)
        message_id = msg.get('Message-ID', '')
        date_hdr = msg.get('Date', '')
        try:
            ts = datetime.datetime.strptime(
                date_hdr, '%a, %d %b %Y %H:%M:%S %z')
            timestamp = ts.isoformat()
        except (ValueError, TypeError):
            timestamp = datetime.datetime.now().isoformat()

        process_email(msg, message_id, timestamp, config, db, templates,
                      dry_run)


def check_range(tree, config, db, templates, since, until):
    fetch_tree(tree)
    cmd = ['log', '--format=%h', f'{tree.remote}/{tree.branch}',
           f'--after={since}', f'--before={until}', '--reverse']
    hashes = tree.git(cmd)
    hashes = hashes.split()
    print(f'Scanning {len(hashes)} emails in range {since} to {until}')

    decisions = []
    senders = set()

    for h in hashes:
        tree.git(['checkout', h])
        msg_path = os.path.join(tree.path, 'm')
        if not os.path.exists(msg_path):
            continue

        msg = parse_email(msg_path)
        message_id = msg.get('Message-ID', '')
        date_hdr = msg.get('Date', '')
        try:
            ts = datetime.datetime.strptime(
                date_hdr, '%a, %d %b %Y %H:%M:%S %z')
            timestamp = ts.isoformat()
        except (ValueError, TypeError):
            timestamp = datetime.datetime.now().isoformat()

        from_hdr = msg.get('From', '')
        _, email_addr = split_from(from_hdr)
        if email_addr:
            senders.add(email_addr)

        process_email(msg, message_id, timestamp, config, db, templates,
                      dry_run=True, decisions=decisions)

    sent = [d for d in decisions if d[0] in
            ('welcome', 'resubmit-warn', 'threaded-warn')]
    skipped = [d for d in decisions if d[0].startswith('skip-')]

    cur = db.conn.cursor()
    cur.execute("SELECT COUNT(*) FROM submission")
    sub_count = cur.fetchone()[0]
    pv_count = len(db.get_pv_bot_actions())

    print()
    print('=== Dry-run Summary ===')
    print(f'Emails scanned:     {len(hashes)}')
    print(f'Unique senders:     {len(senders)}')
    print(f'Submissions:        {sub_count}')
    print(f'pv-bot annotations: {pv_count}')
    print()
    print(f'Would send {len(sent)} email(s):')
    for kind, addr, title in sent:
        print(f'  [{kind}] {addr}  --  {title}')
    print()
    print(f'Considered but skipped {len(skipped)}:')
    for kind, addr, title in skipped:
        print(f'  [{kind}] {addr}  --  {title}')


def main():
    parser = argparse.ArgumentParser(
        description='Mailing list agent for tracking submissions and authors')
    parser.add_argument('--dry-run', action='store_true',
                        help='Process a time range without sending emails')
    parser.add_argument('--since', help='Start date for dry-run (YYYY-MM-DD)')
    parser.add_argument('--until', help='End date for dry-run (YYYY-MM-DD)')
    parser.add_argument('--db-path', help='Override database path')
    parser.add_argument('--config', nargs='*',
                        default=['nipa.config', 'ml-agent.config'],
                        help='Config files to read')
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config)

    if args.db_path:
        db_path = args.db_path
    elif args.dry_run:
        db_path = ':memory:'
    else:
        try:
            db_path = config.get('ml-agent', 'db-path')
        except (configparser.NoSectionError, configparser.NoOptionError):
            db_path = os.path.join(AGENT_DIR, 'ml_agent.db')

    if args.dry_run:
        log_init('org', '/dev/null')
    else:
        log_init('stdout', '')

    db = AgentDB(db_path)
    templates = load_templates()

    try:
        repo_path = config.get('ml-agent-mail-repo', 'path')
        remote = config.get('ml-agent-mail-repo', 'remote')
        branch = config.get('ml-agent-mail-repo', 'branch',
                            fallback='master')
    except (configparser.NoSectionError, configparser.NoOptionError):
        print('ERROR: [ml-agent-mail-repo] section missing from config')
        sys.exit(1)

    tree = Tree('ml-agent', '', repo_path, remote=remote, branch=branch)

    if args.dry_run:
        since = args.since or '2000-01-01'
        until = args.until or '2099-12-31'
        check_range(tree, config, db, templates, since, until)
        db.close()
        return

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)

    while not should_stop:
        req_time = datetime.datetime.now()
        check_new(tree, config, db, templates)

        secs = 120 - (datetime.datetime.now() - req_time).total_seconds()
        while secs > 0 and not should_stop:
            time.sleep(3)
            secs -= 3

    db.close()


if __name__ == "__main__":
    main()
