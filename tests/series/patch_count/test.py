# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

from typing import Tuple
""" Test number of patches, we have a 15 patch limit on netdev """


def patch_count(tree, thing, result_dir) -> Tuple[int, str]:
    if len(thing.patches) <= 15:
        return 0, ""
    if thing.cover_pull:
        return 250, "Series longer than 15 patches"
    # Really no good if there's no cover letter.
    return 1, "Series longer than 15 patches (and no cover letter)"
