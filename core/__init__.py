# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

"""The core module

This module contains all the core classes. Front ends may inherit those
or attach more metadata as needed.

The code here should not contain any front end specific info.

Constants
---------
CORE_DIR : str
    path to the core module's location in the filesystem
NIPA_DIR : str
    path to the location of NIPA sources in the filesystem
"""

import os

from .logger import log, log_open_sec, log_end_sec, log_init
from .patch import Patch
from .test import Test
from .tree import Tree, PullError
from .tester import Tester, write_tree_selection_result, mark_done
from .series import Series

CORE_DIR = os.path.dirname(os.path.abspath(__file__))
NIPA_DIR = os.path.abspath(os.path.join(CORE_DIR, ".."))
