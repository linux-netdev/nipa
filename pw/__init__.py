# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

"""The patchwork module

Module containing Patchwork-related classes, this includes classes used
to communicate with Patchwork as well as descendant classes representing
core objects retrieved from Patchwork.
"""

from .patchwork import Patchwork, PatchworkCheckState
from .pw_series import PwSeries
