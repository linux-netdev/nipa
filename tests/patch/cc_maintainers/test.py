# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

from typing import Tuple
import email
import subprocess
import tempfile
import re

""" Test if relevant maintainers were CCed """

emailpat = re.compile(r'([^ <]*@[^ >]*)')


def cc_maintainers(tree, thing, result_dir) -> Tuple[int, str]:
    patch = thing

    msg = email.message_from_string(patch.raw_patch)
    addrs = msg['to'].split(',')
    addrs += msg['cc'].split(',')
    addrs += msg['from'].split(',')
    included = set()
    for a in addrs:
        match = emailpat.search(a)
        if match:
            included.add(match.group(1))

    expected = set()
    with tempfile.NamedTemporaryFile() as fp:
        patch.write_out(fp)
        command = ['./scripts/get_maintainer.pl', fp.name]
        with subprocess.Popen(command, cwd=tree.path, stdout=subprocess.PIPE) as p:
            line = p.stdout.readline().decode('utf8')
            while line:
                match = emailpat.search(line)
                if match:
                    expected.add(match.group(1))
                line = p.stdout.readline().decode('utf8')
            p.wait()

    # Don't expect people to CC LKML on everything
    if 'linux-kernel@vger.kernel.org' in expected:
        expected.remove('linux-kernel@vger.kernel.org')

    found = expected.intersection(included)
    missing = expected.difference(included)
    if len(missing):
        return 250, f"{len(missing)} maintainers not CCed: {' '.join(missing)}"
    return 0, f"CCed {len(found)} of {len(expected)} maintainers"
