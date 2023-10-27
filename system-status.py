#!/usr/bin/env python3
# SPDX-License-Identifier: ((GPL-2.0 WITH Linux-syscall-note) OR BSD-3-Clause)

import os
import re
import sys
import subprocess
import json


char_filter = re.compile(r'["<>&;]+')


def kv_to_dict(lines):
    data = {}
    for line in lines:
        entry = line.split("=", 1)
        if len(entry) < 2:
            continue
        data[entry[0]] = entry[1]
    return data


def add_one_service(result, name):
    lines = subprocess.check_output(["systemctl", "show", name]).decode('utf-8').split('\n')
    data = kv_to_dict(lines)
    keys = ['CPUUsageNSec', 'MemoryCurrent', 'ActiveState', 'SubState', 'TasksCurrent', 'TriggeredBy', 'Result']
    filtered = {}
    for k in keys:
        filtered[k] = data.get(k, 0)
    result['services'][name] = filtered


def add_one_tree(result, pfx, name):
    global char_filter

    with open(os.path.join(pfx, name), 'r') as fp:
        lines = fp.readlines()
    last = None
    sub = ''
    for line in lines:
        if 'Testing patch' in line:
            last = re.sub(char_filter, "", line)
            sub = ''
        elif 'Running test ' in line:
            sub = line[17:].strip()
        if 'Checking barrier' in line:
            last = None
    if last:
        last += f' ({sub})'
    result['runners'][name] = last


def main():
    cfg = {}
    with open(sys.argv[1], 'r') as fp:
        cfg = json.load(fp)

    result = {'services': {}, 'runners': {}}
    for name in cfg["services"]:
        add_one_service(result, name)
    for name in cfg["trees"]:
        add_one_tree(result, cfg["tree-path"], name)

    with open(sys.argv[2], 'w') as fp:
        json.dump(result, fp)


if __name__ == "__main__":
    main()
