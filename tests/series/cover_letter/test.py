# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

from typing import Tuple
""" Test presence of a cover letter """


def cover_letter(tree, thing, result_dir) -> Tuple[int, str]:
    if thing.cover_letter:
        return 0, "Series has a cover letter"
    if thing.cover_pull:
        return 0, "Pull request is its own cover letter"
    if len(thing.patches) < 3:  # Why is "2" okay here?
        return 0, "Single patches do not need cover letters"
    return 250, "Series does not have a cover letter"
