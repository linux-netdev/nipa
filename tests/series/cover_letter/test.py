# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" Test representation """
# TODO: document


def cover_letter(tree, thing, result_dir):
    if len(thing.patches) < 3 or thing.cover_letter:
        return 0, "", ""
    return 1, "", "Series does not have a cover letter"
