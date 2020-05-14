# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" Test representation """
# TODO: document


def fixes_present(tree, thing, result_dir):
    if tree.pfx.count("next"):
        return 0, ""
    for patch in thing.patches:
        if patch.raw_patch.count('\nFixes: '):
            return 0, ""
    return 1, "Series targets non-next tree, but doesn't contain any Fixes tags"
