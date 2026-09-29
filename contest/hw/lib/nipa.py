# SPDX-License-Identifier: GPL-2.0

"""Re-exports from the main NIPA tree for use by contest/hw/ code.

This module handles the sys.path setup so that other modules in
contest/hw/ can simply ``from lib.nipa import ...`` without repeating
the path manipulation.

Fetcher and NipaLifetime need the dependencies of the services, they are
imported on first use, so the worker on a DUT can use the rest.
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
from contest.remote.lib.names import namify  # noqa: E402, F401


def __getattr__(name):
    if name == 'Fetcher':
        from contest.remote.lib.fetcher import Fetcher
        globals()['Fetcher'] = Fetcher
    elif name == 'NipaLifetime':
        from core import NipaLifetime
        globals()['NipaLifetime'] = NipaLifetime
    else:
        raise AttributeError(name)
    return globals()[name]
