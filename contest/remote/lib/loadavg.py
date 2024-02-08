# SPDX-License-Identifier: GPL-2.0

import os
import time


def wait_loadavg(target, check_ival=30):
    while target is not None:
        load, _, _ = os.getloadavg()

        if load <= target:
            break

        print(f"Waiting for loadavg to decrease: {load} > {target}")
        time.sleep(check_ival)
