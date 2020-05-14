# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" Test representation """
# TODO: document


def patch_count(tree, thing, result_dir):
    if len(thing.patches) <= 15:
        return 0, ""
    return 1, "Series longer than 15 patches"
