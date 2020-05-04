#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.
# Copyright (c) 2020 Facebook

import configparser
import os
import time
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from pw import Patchwork, PatchworkCheckState

# TODO: document

PW = None
CONFIG = None


def is_int(s):
    try:
        int(s)
        return True
    except ValueError:
        return False


def _pw_upload_results(task_dir, pw, config):
    result_server = config.get('results', 'server', fallback='file:///')
    for root, dirs, _ in os.walk(task_dir):
        for patch in dirs:
            # TODO: do something with series checks
            if not is_int(patch):
                continue

            patch_dir = os.path.join(root, patch)
            for _, test_dirs, _ in os.walk(patch_dir):
                for test in test_dirs:
                    url = f"{result_server}/{series}/{patch}/{test}"

                    state_path = os.path.join(patch_dir, test, "retcode")
                    with open(state_path, "r") as f:
                        if f.read() == "0":
                            state = PatchworkCheckState.SUCCESS
                        else:
                            state = PatchworkCheckState.FAIL

                    pw.post_check(patch=patch, name=test, state=state,
                                  url=url, desc="Link")
        break

    os.mknod(os.path.join(task_dir, ".pw_done"))


def pw_upload_results(series_dir, pw, config):
    log_open_sec('Upload initial')
    try:
        _pw_upload_results(series_dir, pw, config)
    finally:
        log_end_sec()


def _initial_scan_series(series_root, pw, config):
    for root, dirs, _ in os.walk(series_root):
        for task in dirs:
            path = os.path.join(root, task)
            if not os.path.exists(os.path.join(path, 'done')):
                log(f"No task result for {task}")
                continue
            if os.path.exists(os.path.join(path, 'done_pw')):
                log(f"Already uploaded {task}")
                continue
            pw_upload_results(path, pw, config)
        break


def _initial_scan(results_dir, pw, config):
    for root, dirs, _ in os.walk(results_dir):
        for series in dirs:
            _initial_scan_series(series, pw, config)
        break


def initial_scan(results_dir, pw, config):
    log_open_sec('Upload initial')
    try:
        _initial_scan(results_dir, pw, config)
    finally:
        log_end_sec()


def on_created(event):
    global PW

    task_dir = os.path.dirname(event.src_path)
    log('Async event for ' + event.src_path)
    pw_upload_results(task_dir, PW, CONFIG)


def watch_scan(results_dir, pw, config):
    global PW, CONFIG

    PW = pw
    CONFIG = config

    event_handler = PatternMatchingEventHandler(patterns=['*done'],
                                                ignore_patterns=[],
                                                ignore_directories=True,
                                                case_sensitive=True)
    event_handler.on_created = on_created

    observer = Observer()
    observer.schedule(event_handler, results_dir, recursive=True)

    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        observer.join()


def main():
    # Init state
    config = configparser.ConfigParser()
    config.read(['nipa.config', 'pw.config', 'upload.config'])

    log_init(config.get('log', 'type', fallback='org'),
             config.get('log', 'file', fallback=os.path.join(NIPA_DIR,
                                                             "upload.org")))

    results_dir = config.get('results', 'dir',
                             fallback=os.path.join(NIPA_DIR, "results"))

    pw = Patchwork(config)

    # Initial walk
    initial_scan(results_dir, pw, config)
    # Watcher
    watch_scan(results_dir, pw, config)


if __name__ == "__main__":
    main()
