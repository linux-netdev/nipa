# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

# TODO: document

import re

from core import log, log_open_sec, log_end_sec


def series_tree_name_direct(series):
    for t in ['net-next', 'net']:
        if re.match(r'\[.*{pfx}.*\]'.format(pfx=t), series.subject):
            return t


def _file_name_match_start(pfx, fn):
    return fn.startswith(pfx)


def _file_name_match_dotted(pfx, fn):
    dirs = pfx.split('/')
    while True:
        dirs.pop(0)
        dotted = '.../' + '/'.join(dirs)
        if dotted == '.../':
            return False

        if fn.startswith(dotted):
            return True


def _tree_name_should_be_local_files(raw_email):
    acceptable_files = {
        'CREDITS',
        'MAINTAINERS',
        'Documentation/',
        'include/',
    }
    required_files = {
        'include/net/',
        'net/',
        'drivers/net/',
        'drivers/net/ethernet/',
        'tools/testing/selftests/net/'
    }
    excluded_files = {
        'drivers/net/wireless/',
    }
    all_files = acceptable_files.union(required_files)
    required_found = False

    lines = raw_email.split('\n')
    regex = re.compile(r'^\s*([-\w/._]+)\s+\|\s+\d+\s*[-+]*\s*$')
    for line in lines:
        match = regex.match(line)
        if not match:
            continue

        found = False
        excluded = False
        file_name = match.group(1)
        log_open_sec(f'Checking file name {file_name}')
        if file_name.startswith('.../'):
            compare = _file_name_match_dotted
        else:
            compare = _file_name_match_start

        for fn in excluded_files:
            excluded = excluded or compare(fn, file_name)
            if excluded:
                log(f'Excluded by {fn}', "")
                break
        for fn in all_files:
            matches = compare(fn, file_name)
            if not matches:
                continue
            log(f'Matched by {fn}', "")
            found = True
            if not excluded:
                required_found = required_found or fn in required_files
        log_end_sec()
        if not found:
            log(f'File name {file_name} was not matched by any list', "")
            return False

    if not required_found:
        log('No required files found', "")
    return required_found


def _tree_name_should_be_local(raw_email):
    return _tree_name_should_be_local_files(raw_email)


def series_tree_name_should_be_local(series):
    if series.cover_letter:
        return _tree_name_should_be_local(series.cover_letter)
    for p in series.patches:
        if not _tree_name_should_be_local(p.raw_patch):
            return False
    return True


def _ignore_missing_tree_name(subject):
    log(f'checking ignore for {subject}', "")
    return subject.count('] can: ') != 0 or \
        subject.count('pull-request:') != 0 or \
        subject.count('[GIT.*]')


def series_ignore_missing_tree_name(series):
    if series.cover_letter:
        return _ignore_missing_tree_name(series.subject)
    for p in series.patches:
        if not _ignore_missing_tree_name(p.subject):
            return False
    return True


def series_is_a_fix_for(s, tree):
    commits = []
    regex = re.compile(r'^Fixes: [a-f0-9]* \(')
    for p in s.patches:
        commits += regex.findall(p.raw_patch)
    for c in commits:
        if not tree.contains(c):
            return False

    return commits and tree.check_applies(s)
