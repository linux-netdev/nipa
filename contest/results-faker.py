#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import datetime
import json
import os


"""
Combined test runner and collector.
It generates fake data for the UI to display.
It holds no history, only live branches will show up.
"""


def main() -> None:
    with open(os.sys.argv[1], "r") as fp:
        branches = json.load(fp)

    results = []
    for br in branches:
        data = {"executor": "faker", "remote": "local",
                "branch": br["branch"]}

        br_dt = datetime.datetime.fromisoformat(br["date"])
        br_dt += datetime.timedelta(minutes=2)
        data["start"] = br_dt.isoformat()
        br_dt += datetime.timedelta(minutes=3)
        data["end"] = br_dt.isoformat()

        data["results"] = [
            {"test": "branch-created", "group": "fake", "result": "pass",
             "link": "https://netdev.bots.linux.dev/static/nipa/branches.json"}
        ]

        results.append(data)

    with open(os.sys.argv[2], "w") as fp:
        json.dump(results, fp)


if __name__ == "__main__":
    main()
