# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

from typing import Tuple
""" Test presence of a cover letter """
""" Test number of patches, we have a 15 patch limit on netdev """
""" Test if subject prefix (tree designation) is present """


def cover_letter(tree, thing, result_dir) -> Tuple[int, str]:
    if thing.cover_letter:
        return 0, ""
    if thing.cover_pull:
        return 0, "Pull request is its own cover letter"
    # 2 patches are okay without a cover letter; this covers trivial cases like
    # feature + selftest postings where commit message of the feature is definitive
    if len(thing.patches) < 3:
        return 0, "Single patches do not need cover letters"
    return 250, "Series does not have a cover letter"


def patch_count(tree, thing, result_dir) -> Tuple[int, str]:
    if len(thing.patches) <= 15:
        return 0, ""
    if thing.cover_pull:
        return 250, "Series longer than 15 patches"
    # Really no good if there's no cover letter.
    return 1, "Series longer than 15 patches (and no cover letter)"


def subject_prefix(tree, thing, result_dir) -> Tuple[int, str]:
    if thing.tree_mark_expected and not thing.tree_marked:
        return 250, "Target tree name not specified in the subject"
    return 0, ""


def series_format(tree, thing, result_dir) -> Tuple[int, str]:
    res = [
        cover_letter(tree, thing, result_dir),
        patch_count(tree, thing, result_dir),
        subject_prefix(tree, thing, result_dir)
    ]

    code = 0
    msg = ""
    for r in res:
        if r[0] == 1:
            code = 1
        elif r[0] == 250 and code == 0:
            code = 250
        if r[1]:
            if msg:
                msg += '; '
            msg += r[1]
    if msg == "":
        msg = "Posting correctly formatted"

    return code, msg
