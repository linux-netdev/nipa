# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

from typing import Tuple
""" Test presence of a cover letter """


def cover_letter(tree, thing, result_dir) -> Tuple[int, str]:
    if len(thing.patches) < 3 or thing.cover_letter:
        return 0, ""
    if thing.cover_pull:
        return 0, "Pull request"
    return 250, "Series does not have a cover letter"
