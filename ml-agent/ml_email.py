# SPDX-License-Identifier: GPL-2.0
# pylint: disable=missing-module-docstring,missing-function-docstring
# pylint: disable=wrong-import-position,import-error

import email
import re
import sys
import os

from email.policy import default as default_policy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.maintainers import Person


def parse_email(msg_path):
    with open(msg_path, 'rb') as fp:
        raw = fp.read()
    return email.message_from_bytes(raw, policy=default_policy)


def parse_email_str(raw_str):
    return email.message_from_string(raw_str, policy=default_policy)


def split_from(from_hdr):
    if not from_hdr:
        return '', ''
    return Person.name_email_split(from_hdr)


def get_body(msg):
    if msg.is_multipart():
        body = msg.get_body(preferencelist=('plain',))
        if body is None:
            return ''
        try:
            return body.as_string()
        except LookupError:
            return ''
    payload = msg.get_payload()
    return payload if isinstance(payload, str) else ''


def extract_bracket_contents(subject):
    if not subject or subject[0] != '[':
        return None
    end = subject.find(']')
    if end == -1:
        return None
    return subject[1:end]


def extract_title(subject):
    if not subject:
        return ''
    return re.sub(r'^(\[.*?\]\s*)+', '', subject).strip()


def extract_version(bracket_contents):
    if not bracket_contents:
        return None
    m = re.search(r'\bv(\d+)\b', bracket_contents)
    if m:
        return int(m.group(1))
    return None


def is_reply(msg):
    return bool(msg.get('In-Reply-To', '') or msg.get('References', ''))


def is_submission(msg):
    subject = msg.get('Subject', '')
    bracket = extract_bracket_contents(subject)
    if bracket is None:
        return False
    upper = bracket.upper()
    if 'PATCH' not in upper and 'RFC' not in upper:
        return False
    return not is_reply(msg)


def is_resubmission_candidate(msg):
    subject = msg.get('Subject', '')
    bracket = extract_bracket_contents(subject)
    if bracket is None:
        return False
    upper = bracket.upper()
    if 'PATCH' not in upper:
        return False
    if 'RFC' in upper:
        return False
    version = extract_version(bracket)
    if version is None or version < 1:
        return False
    # Skip individual patches in a series (e.g. "1/3"); only the cover
    # letter ("0/N") or unnumbered submissions count as a resubmission.
    m = re.search(r'\b(\d+)/\d+\b', bracket)
    if m and int(m.group(1)) != 0:
        return False
    return is_reply(msg)


def extract_pv_bot_tags(msg):
    body = get_body(msg)
    if not body:
        return []
    tags = []
    for line in body.split('\n'):
        if line.startswith('pv-bot:'):
            tag = line[7:].strip()
            if tag:
                tags.append(tag)
    return tags
