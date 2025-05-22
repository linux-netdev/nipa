# SPDX-License-Identifier: GPL-2.0

import os
import time


def get_dirty_mem():
    """ Get amount of dirty mem, returns value in MB """
    with open("/proc/meminfo", "r") as fp:
        lines = fp.read().split("\n")
        dirty = list(filter(lambda a: "Dirty" in a, lines))[0]
        return int(dirty.split(" ")[-2]) / 1000


def wait_loadavg(target, dirty_max=100, check_ival=30, stable_cnt=3):
    """
    Wait for loadavg to drop but be careful at the start, the load
    may have not ramped up, yet, so if we ungate early whoever is waiting
    will experience the overload.
    """

    seen_stable = 0
    while target is not None:
        load, _, _ = os.getloadavg()
        dirty = get_dirty_mem()

        if load <= target and dirty <= dirty_max:
            if seen_stable >= stable_cnt:
                break
            seen_stable += 1
        else:
            seen_stable = 0

        print(f"Waiting for loadavg to decrease: CPU: {load} > {target} Dirty Mem: {dirty} > {dirty_max} MB ({seen_stable})")
        time.sleep(check_ival)
