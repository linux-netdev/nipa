# SPDX-License-Identifier: GPL-2.0

"""Remote runner helpers with lazy imports for optional dependencies."""

from .cbarg import CbArg
from .crash import extract_crash, has_crash
from .names import namify
from .results import guess_indicators, parse_nested_tests
from .results import result_from_indicators


__all__ = [
    'CbArg', 'Fetcher', 'VM', 'extract_crash', 'guess_indicators', 'has_crash',
    'namify', 'new_vm', 'parse_nested_tests', 'result_from_indicators',
    'wait_loadavg',
]


def __getattr__(name):
    if name == 'Fetcher':
        from .fetcher import Fetcher
        globals()['Fetcher'] = Fetcher
    elif name == 'wait_loadavg':
        from .loadavg import wait_loadavg
        globals()['wait_loadavg'] = wait_loadavg
    elif name in ('VM', 'new_vm'):
        from .vm import VM, new_vm
        globals().update(VM=VM, new_vm=new_vm)
    else:
        raise AttributeError(name)
    return globals()[name]
