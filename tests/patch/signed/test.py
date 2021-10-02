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
    if p.returncode < 16:
        # If patatt returns less than RES_ERROR, lower result to a warning.
        ret = 250
        # $ cat tests/patch/signed/selftest/nokey.mbox | patatt validate
        #  NOKEY | keescook@chromium.org, -
        #        | keescook@chromium.org/default no matching openpgp key found
        # $ echo $?
        # 8
        if 'NOKEY' in reason:
            reason = f"Signing key not found in keyring: {reason}"
        # $ cat tests/patch/signed/selftest/nosig.mbox | patatt validate
        #  NOSIG | -
        #        | no signatures found
        if 'NOSIG' in reason:
            reason = "No signature found. Please sign patches: https://github.com/mricon/patatt"
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
            # $ cat tests/patch/signed/selftest/good.mbox | patatt validate
            #   PASS | keescook@chromium.org, -
            if msg == 'PASS':
                good += 1
            else:
                bad += 1
        # Now check for any bad statuses.
        if bad == 0:
            if good == 0:
                # If there is nothing in stdout then no validation happened (no signature)
                # (As seen with patatt < 5.0)
                # $ cat tests/patch/signed/selftest/nosig.mbox | patatt validate
                # $ echo $?
                # 0
                ret = 250
                reason = "No signature found. Please sign patches: https://github.com/mricon/patatt"
            if good > 0:
                ret = 0
        else:
            # $ cat tests/patch/signed/selftest/bad.mbox | patatt validate
            # BADSIG | keescook@chromium.org, -
            #        | failed to validate using /home/nipa-user/trusted/kernel/pgpkeys/.keyring/openpgp/chromium.org/keescook/default
            ret = 1

    return ret, reason
