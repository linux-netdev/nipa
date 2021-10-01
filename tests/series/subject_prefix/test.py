# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

from typing import Tuple
""" Test if subject prefix (tree designation) is present """


def subject_prefix(tree, thing, result_dir) -> Tuple[int, str]:
    if thing.tree_mark_expected and not thing.tree_marked:
        return 250, "Target tree name not specified in the subject"
    return 0, ""
