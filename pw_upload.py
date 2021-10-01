#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.
# Copyright (c) 2020 Facebook

import configparser
import os
import signal
import inotify_simple as inotify

from core import NIPA_DIR
from core import log, log_open_sec, log_end_sec, log_init
from pw import Patchwork, PatchworkCheckState

# TODO: document
should_stop = False


def handler(signum, _):
    global should_stop

    print('Signal handler called with signal', signum)
    should_stop = True


def is_int(s):
    try:
        int(s)
        return True
    except ValueError:
        return False


class PwTestResult:
    def __init__(self, test_name: str, root_dir: str, url: str):
        self.test = test_name
        self.url = url

        try:
            with open(os.path.join(root_dir, test_name, "retcode"), "r") as f:
                retcode = f.read()
                if retcode == "0":
                    self.state = PatchworkCheckState.SUCCESS
                elif retcode == "250":
                    self.state = PatchworkCheckState.WARNING
                else:
                    self.state = PatchworkCheckState.FAIL
        except FileNotFoundError:
            self.state = PatchworkCheckState.FAIL

        try:
            with open(os.path.join(root_dir, test_name, "desc"), "r") as f:
                self.desc = f.read()
        except FileNotFoundError:
            self.desc = "Link"


def _pw_upload_results(series_dir, pw, config):
    series = os.path.basename(series_dir)
    result_server = config.get('results', 'server', fallback='https://google.com')

    # Collect series checks first
    series_results = []
    for root, dirs, _ in os.walk(series_dir):
        for test in dirs:
            if is_int(test):
                continue

            tr = PwTestResult(test, series_dir, f"{result_server}/{series}/{test}")
            series_results.append(tr)

        break

    log(f"Found {len(series_results)} series results")

    for root, dirs, _ in os.walk(series_dir):
        for patch in dirs:
            if not is_int(patch):
                continue

            for tr in series_results:
                pw.post_check(patch=patch, name=tr.test, state=tr.state, url=tr.url, desc=tr.desc)

            patch_dir = os.path.join(root, patch)
            for _, test_dirs, _ in os.walk(patch_dir):
                for test in test_dirs:
                    tr = PwTestResult(test, patch_dir, f"{result_server}/{series}/{patch}/{test}")
                    pw.post_check(patch=patch, name=tr.test, state=tr.state, url=tr.url,
                                  desc=tr.desc)

                log(f"Patch {patch} - found {len(test_dirs)} results")
                break
        break


def pw_upload_results(series_dir, pw, config):
    log_open_sec(f'Upload results for {os.path.basename(series_dir)}')
    try:
        _pw_upload_results(series_dir, pw, config)
    finally:
        log_end_sec()


def pw_upload_results_cb(series_dir, ctx):
    pw_upload_results(series_dir, ctx['pw'], ctx['config'])


class TestWatcher(object):
    def __init__(self, base_path, trigger, complete, cb, cb_ctx):
        self.base_path = base_path
        self.trigger = trigger
        self.complete = complete
        self.cb = cb
        self.cb_ctx = cb_ctx

        self.wd2name = {}
        self.inotify = inotify.INotify()
        self.main_wd = None

    def _complete_dir(self, wd):
        log(f"Dir {self.wd2name[wd]} ({wd}) has been processed", "")
        self.inotify.rm_watch(wd)
        self.wd2name.pop(wd)

    def _trigger_dir(self, name):
        # Double check if completed, we can come here from notification
        # after initial scan already processed the trigger
        complete = os.path.join(self.base_path, name, self.complete)
        if os.path.exists(complete):
            log(f'Dir {name} already processed', '')
            return

        log(f"Trigger for dir {name}", "")
        self.cb(os.path.join(self.base_path, name), self.cb_ctx)
        os.mknod(complete)

    def _handle_new_dir(self, name):
        path = os.path.join(self.base_path, name)
        trigger = os.path.join(path, self.trigger)
        complete = os.path.join(path, self.complete)

        # Fast path already processed, assume we're the only entity
        # creating 'complete' markers
        if os.path.exists(complete):
            log(f'Dir {name} already processed', '')
            return

        # Install the watch, to avoid race conditions with the check
        wd = self.inotify.add_watch(path, inotify.flags.CREATE)
        self.wd2name[wd] = name
        log(f"New watch: {wd} => {name}", '')

        if os.path.exists(trigger):
            self._trigger_dir(name)

    def initial_scan(self):
        # Install the watch first
        flags = inotify.flags.CREATE | inotify.flags.ISDIR
        self.main_wd = self.inotify.add_watch(self.base_path, flags)
        self.wd2name[self.main_wd] = ''
        # Then scan the fs tree
        for root, dirs, _ in os.walk(self.base_path):
            for d in dirs:
                self._handle_new_dir(d)
            break

    def watch(self):
        global should_stop

        if self.main_wd is None:
            raise Exception('Not initialized')

        while not should_stop:
            for event in self.inotify.read(timeout=2):
                if event.mask & inotify.flags.IGNORED or \
                   event.wd < 0 or \
                   event.wd not in self.wd2name:
                    continue

                if event.wd == self.main_wd:
                    if event.mask & inotify.flags.ISDIR:
                        self._handle_new_dir(event.name)
                else:  # subdir
                    print(f'File event for {self.wd2name[event.wd]} => {event.name}')
                    if event.name == self.trigger:
                        self._trigger_dir(self.wd2name[event.wd])
                    elif event.name == self.complete:
                        self._complete_dir(event.wd)


def main():
    # Init state
    config = configparser.ConfigParser()
    config.read(['nipa.config', 'pw.config', 'upload.config'])

    log_init(config.get('log', 'type', fallback='org'),
             config.get('log', 'file', fallback=os.path.join(NIPA_DIR, "upload.org")),
             force_single_thread=True)

    results_dir = config.get('results', 'dir', fallback=os.path.join(NIPA_DIR, "results"))

    pw = Patchwork(config)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    tw = TestWatcher(results_dir, '.tester_done', '.pw_done', pw_upload_results_cb, {
        'pw': pw,
        'config': config
    })
    tw.initial_scan()
    tw.watch()


if __name__ == "__main__":
    main()
