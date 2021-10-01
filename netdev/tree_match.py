# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

# TODO: document

import re

from core import log, log_open_sec, log_end_sec


def series_tree_name_direct(series):
    for t in ['net-next', 'net', 'bpf-next', 'bpf']:
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
    """
    Returns True: patch should have been explicitly designated for local tree
            False: patch has nothing to do with local trees
            None: patch has mixed contents, it touches local code, but also code outside
    """
    acceptable_files = {
        '.../',
        'CREDITS',
        'MAINTAINERS',
        'Documentation/',
        'include/',
    }
    required_files = {
        'Documentation/networking/',
        'include/linux/netdevice.h',
        'include/linux/skbuff.h',
        'include/net/',
        'include/phy/',
        'net/',
        'drivers/atm/',
        'drivers/net/',
        'drivers/dsa/',
        'drivers/nfc/',
        'drivers/phy/',
        'drivers/net/ethernet/',
        'tools/testing/selftests/net/',
    }
    excluded_files = {
        'drivers/net/wireless/',
    }
    all_files = acceptable_files.union(required_files)
    required_found = False
    foreign_found = False

    lines = raw_email.split('\n')
    r_diffstat = re.compile(r'^\s*([-\w/._]+)\s+\|\s+\d+\s*[-+]*\s*$')
    r_header = re.compile(r'\+\+\+ b/([-\w/._]+)$')
    for line in lines:
        match = r_header.match(line)
        if not match:
            match = r_diffstat.match(line)
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
            foreign_found = True

    log(f'Required found: {required_found}, foreign_found: {foreign_found}', "")
    if not required_found:
        return False
    if foreign_found:
        return None
    return True


def _tree_name_should_be_local(raw_email):
    return _tree_name_should_be_local_files(raw_email)


def series_tree_name_should_be_local(series):
    all_local = True
    some_local = False
    for p in series.patches:
        ret = _tree_name_should_be_local(p.raw_patch)
        # Returns tri-state True, None, False. And works well:
        #     True and None -> None
        #     True and False -> False
        #     False and None -> False
        all_local = all_local and ret
        #     True or None  -> True
        #     True or False -> True
        #     False or None -> False
        some_local = some_local or ret
    return all_local, some_local


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
