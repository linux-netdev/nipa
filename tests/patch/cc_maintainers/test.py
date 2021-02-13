# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

from typing import Tuple
import email
import email.utils
import subprocess
import tempfile
import re

""" Test if relevant maintainers were CCed """

emailpat = re.compile(r'([^ <"]*@[^ >"]*)')


def cc_maintainers(tree, thing, result_dir) -> Tuple[int, str]:
    patch = thing

    msg = email.message_from_string(patch.raw_patch)
    addrs = msg.get_all('to', [])
    addrs += msg.get_all('cc', [])
    addrs += msg.get_all('from', [])
    addrs += msg.get_all('sender', [])
    included = set([e for n, e in email.utils.getaddresses(addrs)])

    expected = set()
    with tempfile.NamedTemporaryFile() as fp:
        patch.write_out(fp)
        command = ['./scripts/get_maintainer.pl', fp.name]
        with subprocess.Popen(command, cwd=tree.path, stdout=subprocess.PIPE) as p:
            line = p.stdout.readline().decode('utf8', 'replace')
            while line:
                match = emailpat.search(line)
                if match:
                    expected.add(match.group(1))
                line = p.stdout.readline().decode('utf8', 'replace')
            p.wait()

    # Don't expect people to CC LKML on everything
    expected.discard('linux-kernel@vger.kernel.org')
    # For new files NIPA will get marked as committer
    expected.discard('nipa@patchwork.hopto.org')

    found = expected.intersection(included)
    missing = expected.difference(included)
    if len(missing):
        ret = 250 if len(found) > 1 else 1
        return ret, f"{len(missing)} maintainers not CCed: {' '.join(missing)}"
    return 0, f"CCed {len(found)} of {len(expected)} maintainers"
