# SPDX-License-Identifier: GPL-2.0

"""Re-exports from the main NIPA tree for use by contest/hw/ code.

This module handles the sys.path setup so that other modules in
contest/hw/ can simply ``from lib.nipa import ...`` without repeating
the path manipulation.
"""

import os
import sys

# Add the NIPA project root so we can import core.*, contest.remote.*, etc.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', '..', '..'))

# pylint: disable=wrong-import-position

from contest.remote.lib.crash import has_crash  # noqa: E402, F401
from contest.remote.lib.crash import extract_crash  # noqa: E402, F401
from contest.remote.lib.crash import crash_finger_print  # noqa: E402, F401
from contest.remote.lib.results import guess_indicators  # noqa: E402, F401
from contest.remote.lib.results import result_from_indicators  # noqa: E402, F401
from contest.remote.lib.results import parse_nested_tests  # noqa: E402, F401
from contest.remote.lib.cbarg import CbArg  # noqa: E402, F401
from contest.remote.lib.fetcher import Fetcher  # noqa: E402, F401
from contest.remote.lib.fetcher import namify  # noqa: E402, F401
from core import NipaLifetime  # noqa: E402, F401
