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
infos=/path/to/infos.json,/path/to/infos2.json
[output]
info=/path/to/info.json
"""

def combine_infos(config):
    paths = config.get("input", "infos", fallback="").split(',')
    if not paths:
        return

    infos = {}
    for path in paths:
        with open(path, "r") as fp:
            infos.update(json.load(fp))

    # atomic write needed
    path = config.get("output", "info")
    tmp = path + '.new'
    with open(tmp, 'w') as fp:
        json.dump(infos, fp)
    os.rename(tmp, path)


def main() -> None:
    config = configparser.ConfigParser()
    config.read(['faker.config'])

    combine_infos(config)


if __name__ == "__main__":
    main()
