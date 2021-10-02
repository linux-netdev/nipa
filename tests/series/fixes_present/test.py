# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

import re

from typing import Tuple
""" Test presence of the Fixes tag in non *-next patches """


def fixes_present(tree, thing, result_dir) -> Tuple[int, str]:
    if tree.pfx.count("next"):
        return 0, "Fixes tag not required for -next series"
    for patch in thing.patches:
        if patch.raw_patch.count('\nFixes: '):
            return 0, "Fixes tag present in non-next series"

    r_header = re.compile(r'\+\+\+ b/([-\w/._]+)$')
    all_safe = None
    for p in thing.patches:
        lines = p.raw_patch.split('\n')
        safe = None

        for line in lines:
            match = r_header.match(line)
            if not match:
                continue

            file_name = match.group(1)
            if file_name.startswith("Documentation/") or \
                file_name.startswith("MAINTAINERS"):
                safe = True
            else:
                safe = False
                break

        if safe:
            all_safe = True
        else:
            all_safe = False
            break
    if all_safe:
        return 0, "No Fixes tags, but series doesn't touch code"

    return 1, "Series targets non-next tree, but doesn't contain any Fixes tags"
