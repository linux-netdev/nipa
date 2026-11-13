# SPDX-License-Identifier: GPL-2.0

"""Stable names for result URLs and database rows."""

import re


def namify(value):
    if not value:
        return 'no-name'
    return re.sub(r'[^0-9a-zA-Z]+', '-', value).rstrip('-')
