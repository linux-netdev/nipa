# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

# TODO: document

import re


def subject_prefix(tree, thing, result_dir):
    if thing.tree_mark_expected and not thing.tree_marked:
        return 1, "", "Target tree name not specified in the subject"
    return 0, "", ""
