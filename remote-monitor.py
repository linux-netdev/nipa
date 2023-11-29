#!/usr/bin/env python3
# SPDX-License-Identifier: ((GPL-2.0 WITH Linux-syscall-note) OR BSD-3-Clause)

import json
import os
import requests
import subprocess
import time


def notify(hdr: str, msg: str) -> None:
    subprocess.check_output(["notify-send", hdr, msg])


def main() -> None:
    while True:
        try:
            r = requests.get(os.sys.argv[1])
            js = json.loads(r.content.decode('utf-8'))

            good = 0
            bad = []
            for name in js["services"]:
                s = js["services"][name]
                if s["ActiveState"] == "active" and s["SubState"] == "running":
                    good += 1
                elif isinstance(s.get("TriggeredBy", 0), str) and s["Result"] == "success":
                    good += 1
                else:
                    bad.append(name)

            if bad:
                notify("NIPA", f"Services in bad state: {bad} (good cnt: {good})")
        except Exception:
            notify("NIPA", "checking status failed")
            raise
        time.sleep(30)


if __name__ == "__main__":
    main()
