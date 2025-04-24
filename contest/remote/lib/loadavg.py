# SPDX-License-Identifier: GPL-2.0

import os
import time


def wait_loadavg(target, check_ival=30, stable_cnt=4):
    """
    Wait for loadavg to drop but be careful at the start, the load
    may have not ramped up, yet, so if we ungate early whoever is waiting
    will experience the overload.
    """
    while target is not None:
        load, _, _ = os.getloadavg()

        if load <= target:
            if stable_cnt == 0:
                break
            stable_cnt -= 1
        else:
            stable_cnt = 0

        print(f"Waiting for loadavg to decrease: {load} > {target} ({stable_cnt})")
        time.sleep(check_ival)
