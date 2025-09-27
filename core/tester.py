# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" The main CI module """

import configparser
import os
import threading
import re

import core
from core import Test, PullError, PatchApplyError


def write_tree_selection_result(result_dir, s, comment):
    series_dir = os.path.join(result_dir, str(s.id))

    tree_test_dir = os.path.join(series_dir, "tree_selection")
    if not os.path.exists(tree_test_dir):
        os.makedirs(tree_test_dir)

    with open(os.path.join(tree_test_dir, "retcode"), "w+") as fp:
        fp.write("0")
    with open(os.path.join(tree_test_dir, "desc"), "w+") as fp:
        fp.write(comment)

    for patch in s.patches:
        patch_dir = os.path.join(series_dir, str(patch.id))
        if not os.path.exists(patch_dir):
            os.makedirs(patch_dir)


def write_apply_result(series_dir, tree, what, retcode):
    series_apply = os.path.join(series_dir, "apply")
    os.makedirs(series_apply)

    core.log("Series " + what, "")
    with open(os.path.join(series_apply, "retcode"), "w+") as fp:
        fp.write(str(retcode))
    with open(os.path.join(series_apply, "desc"), "w+") as fp:
        fp.write(f"Patch {what} to {tree.name}")


def mark_done(result_dir, series):
    series_dir = os.path.join(result_dir, str(series.id))
    if not os.path.exists(os.path.join(series_dir, ".tester_done")):
        os.mknod(os.path.join(series_dir, ".tester_done"))


class Tester(threading.Thread):
    def __init__(self, result_dir, tree, queue, done_queue, config=None):
        threading.Thread.__init__(self)

        self.tree = tree
        self.queue = queue
        self.done_queue = done_queue
        self.should_die = False
        self.result_dir = result_dir
        self.config = config
        self.include = None
        self.exclude = None

        self.series_tests = []
        self.patch_tests = []

    def run(self) -> None:
        if self.config is None:
            self.config = configparser.ConfigParser()
            self.config.read(['nipa.config', 'pw.config', 'tester.config'])

        log_dir = self.config.get('log', 'dir', fallback=core.NIPA_DIR)
        core.log_init(
            self.config.get('log', 'type', fallback='org'),
            self.config.get('log', 'file', fallback=os.path.join(log_dir, f"{self.tree.name}.org")))

        core.log_open_sec("Tester init")
        if not os.path.exists(self.result_dir):
            os.makedirs(self.result_dir)

        tests_dir = os.path.abspath(core.CORE_DIR + "../../tests")
        self.config.set('dirs', 'tests', self.config.get('dirs', 'tests', fallback=tests_dir))

        self.include = [x.strip() for x in re.split(r'[,\n]', self.config.get('tests', 'include', fallback="")) if len(x)]
        self.exclude = [x.strip() for x in re.split(r'[,\n]', self.config.get('tests', 'exclude', fallback="")) if len(x)]

        self.series_tests = self.load_tests("series")
        self.patch_tests = self.load_tests("patch")
        core.log_end_sec()

        while not self.should_die:
            s = self.queue.get()
            if s is None:
                break

            core.log(f"Tester commencing with backlog of {self.queue.qsize()}")
            self.test_series(self.tree, s)
            self.done_queue.put(s)
            core.log("Tester done processing")

        core.log("Tester exiting")

    def get_test_names(self, annotate=True) -> list[str]:
        tests_dir = os.path.abspath(core.CORE_DIR + "../../tests")
        location = self.config.get('dirs', 'tests', fallback=tests_dir)

        self.include = [x.strip() for x in re.split(r'[,\n]', self.config.get('tests', 'include', fallback="")) if len(x)]
        self.exclude = [x.strip() for x in re.split(r'[,\n]', self.config.get('tests', 'exclude', fallback="")) if len(x)]

        tests = []
        for name in ["series", "patch"]:
            tests_subdir = os.path.join(location, name)
            for td in os.listdir(tests_subdir):
                test = f'{name}/{td}'
                if not annotate:
                    pass  # don't annotate
                elif test in self.exclude or \
                     (len(self.include) != 0 and test not in self.include):
                    test += ' [excluded]'
                tests.append(test)

        return tests

    def load_tests(self, name):
        core.log_open_sec(name.capitalize() + " tests")
        tests_subdir = os.path.join(self.config.get('dirs', 'tests'), name)
        tests = []
        for td in os.listdir(tests_subdir):
            test = f'{name}/{td}'
            if test not in self.exclude and (len(self.include) == 0 or test in self.include):
                core.log(f"Adding test {test}")
                tests.append(Test(os.path.join(tests_subdir, td), td))
            else:
                core.log(f"Skipped test {test}")
        core.log_end_sec()

        return tests

    def test_series(self, tree, series):
        write_tree_selection_result(self.result_dir, series, series.tree_selection_comment)
        self._test_series(tree, series)
        mark_done(self.result_dir, series)

    def _test_series(self, tree, series):
        core.log_open_sec("Running tests in tree %s for %s" % (tree.name, series.title))

        series_dir = os.path.join(self.result_dir, str(series.id))
        if not os.path.exists(series_dir):
            os.makedirs(series_dir)
        elif os.path.exists(os.path.join(series_dir, ".tester_done")):
            core.log(f"Already tested in {series_dir}", "")
            core.log_end_sec()
            return

        try:
            tree.reset()
            if series.is_pure_pull():
                self._test_series_pull(tree, series, series_dir)
            else:
                self._test_series_patches(tree, series, series_dir)
        finally:
            core.log_end_sec()

    def _test_series_patches(self, tree, series, series_dir):
        tree.reset(fetch=False)
        try:
            tree.apply(series)
        except PatchApplyError:
            already_applied = tree.check_already_applied(series)
            if already_applied:
                write_apply_result(series_dir, tree, "already applied", 0)
            else:
                write_apply_result(series_dir, tree, "does not apply", 1)
            return

        for test in self.series_tests:
            test.exec(tree, series, series_dir)

        tcnt = 0
        for test in self.patch_tests:
            tcnt += 1
            tree.reset(fetch=False)

            pcnt = 0
            for patch in series.patches:
                pcnt += 1
                cnts = f"{tcnt}/{len(self.patch_tests)}|{pcnt}/{len(series.patches)}"
                core.log_open_sec(f"Testing patch {cnts}| {patch.title}")

                patch_dir = os.path.join(series_dir, str(patch.id))
                if not os.path.exists(patch_dir):
                    os.makedirs(patch_dir)

                try:
                    tree.apply(patch)
                    test.exec(tree, patch, patch_dir)
                except PatchApplyError:
                    write_apply_result(series_dir, tree, f"patch {pcnt} does not apply", 1)
                    return
                finally:
                    core.log_end_sec()

    def _test_series_pull(self, tree, series, series_dir):
        try:
            tree.pull(series.pull_url)
        except PullError:
            series_apply = os.path.join(series_dir, "apply")
            os.makedirs(series_apply)

            core.log("Pull failed", "")
            with open(os.path.join(series_apply, "retcode"), "w+") as fp:
                fp.write("1")
            with open(os.path.join(series_apply, "desc"), "w+") as fp:
                fp.write(f"Pull to {tree.name} failed")
            return

        patch = series.patches[0]

        core.log_open_sec(f"Testing pull request {patch.title}")

        patch_dir = os.path.join(series_dir, str(patch.id))
        if not os.path.exists(patch_dir):
            os.makedirs(patch_dir)

        try:
            for test in self.patch_tests:
                if test.is_pull_compatible():
                    test.exec(tree, patch, patch_dir)
        finally:
            core.log_end_sec()

