# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2021 Kees Cook <keescook@chromium.org>

from typing import Tuple
import subprocess
""" Test if the patch passes signature checks """


def signed(tree, thing, result_dir) -> Tuple[int, str]:
    command = ['patatt', 'validate']
    p = subprocess.run(command, cwd=tree.path, input=thing.raw_patch.encode(),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # Fail closed.
    ret = 1
    reason = p.stdout.decode("utf-8", "replace")
    # If patatt returns less than RES_ERROR, lower result to a warning.
    if p.returncode < 16:
        ret = 250
    if p.returncode == 0:
        # Make sure we see ONLY "PASS" output, even when rc == 0.
        bad = 0
        good = 0
        for line in reason.split('\n'):
            line = line.strip()
            if len(line) == 0:
                # Ignore empty lines.
                continue
            msg, details = line.split('|', 1)
            msg = msg.strip()
            if len(msg) == 0:
                # Ignore lines with empty msg (i.e. informational continuation line).
                continue
            if msg == 'PASS':
                good += 1
            else:
                bad += 1
        # Now check for any bad statuses.
        if bad == 0:
            if good == 0:
                # If there is nothing in stdout then no validation happened (no signature)
                ret = 250
                reason = "No signature found. Please sign patches: https://github.com/mricon/patatt"
            if good > 0:
                ret = 0
        else:
            ret = 1

    return ret, reason
