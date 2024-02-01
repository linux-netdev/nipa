#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import configparser
import datetime
import json
import os


"""
Combined test runner and collector.
It generates fake data for the UI to display.
It holds no history, only live branches will show up.

Config:

[input]
branches=/path/to/branches.json
[output]
dir=/path/to/output
url_pfx=relative/within/server
"""

def main() -> None:
    config = configparser.ConfigParser()
    config.read(['faker.config'])

    with open(config.get("input", "branches"), "r") as fp:
        branches = json.load(fp)

    url = config.get("output", "url_pfx")
    if url[-1] != '/':
        url += '/'
    directory = config.get("output", "dir")

    results = []
    for br in branches:
        br_dt = datetime.datetime.fromisoformat(br["date"])
        run_id_cookie = str(int(br_dt.timestamp() / 60) % 1000000)
        fname = f"results-{run_id_cookie}.json"

        data = {'url': url + fname,
               'branch': br["branch"],
               'executor': "brancher"}
        results.append(data)

        run = {'branch': br["branch"], 'executor': "brancher"}
        br_dt += datetime.timedelta(seconds=1)
        run["start"] = br_dt.isoformat()
        br_dt += datetime.timedelta(seconds=3)
        run["end"] = br_dt.isoformat()

        tail = br["url"].find('.git ')
        if br["url"].startswith('https://github.com') and tail > 0:
            br_url = br["url"][:tail] + "/commits/" + br["url"][tail + 5:]
        else:
            br_url = "https://netdev.bots.linux.dev/static/nipa/branches.json"

        run["results"] = [
            {"test": "branch-created", "group": "---", "result": "pass", "link": br_url}
        ]

        with open(os.path.join(directory, fname), "w") as fp:
            json.dump(run, fp)

    with open(os.path.join(directory, 'results.json'), "w") as fp:
        json.dump(results, fp)


if __name__ == "__main__":
    main()
