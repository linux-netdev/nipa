# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" The main CI module """

import configparser
import os
import threading
import re

import core
from core import Test, PullError


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


def mark_done(result_dir, series):
    series_dir = os.path.join(result_dir, str(series.id))
    if not os.path.exists(os.path.join(series_dir, ".tester_done")):
        os.mknod(os.path.join(series_dir, ".tester_done"))


class Tester(threading.Thread):
    def __init__(self, result_dir, tree, queue, done_queue, barrier):
        threading.Thread.__init__(self)

        self.tree = tree
        self.queue = queue
        self.done_queue = done_queue
        self.barrier = barrier
        self.should_die = False
        self.result_dir = result_dir
        self.config = None

        self.series_tests = []
        self.patch_tests = []

    def run(self) -> None:
        self.config = configparser.ConfigParser()
        self.config.read(['nipa.config', 'pw.config', 'tester.config'])

        core.log_init(
            self.config.get('log', 'type', fallback='org'),
            self.config.get('log', 'file', fallback=os.path.join(core.NIPA_DIR, f"{self.tree.name}.org")))

        core.log_open_sec("Tester init")
        if not os.path.exists(self.result_dir):
            os.makedirs(self.result_dir)

        tests_dir = os.path.abspath(core.CORE_DIR + "../../tests")
        self.config.set('dirs', 'tests', self.config.get('dirs', 'tests', fallback=tests_dir))

        self.series_tests = self.load_tests("series")
        self.patch_tests = self.load_tests("patch")
        core.log_end_sec()

        while not self.should_die:
            self.barrier.wait()

            while not self.should_die and not self.queue.empty():
                s = self.queue.get()
                if s is None:
                    continue
                self.test_series(self.tree, s)
                self.done_queue.put(s)

                # If we're the last worker with work to do - let the poller run
                core.log(f"Checking barrier {self.barrier.n_waiting}/{self.barrier.parties} ")
                if self.barrier.parties == self.barrier.n_waiting + 1:
                    break

            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                break

    def load_tests(self, name):
        core.log_open_sec(name.capitalize() + " tests")
        tests_subdir = os.path.join(self.config.get('dirs', 'tests'), name)
        include = [x.strip() for x in re.split(r'[,\n]', self.config.get('tests', 'include', fallback="")) if len(x)]
        exclude = [x.strip() for x in re.split(r'[,\n]', self.config.get('tests', 'exclude', fallback="")) if len(x)]
        tests = []
        for td in os.listdir(tests_subdir):
            test = f'{name}/{td}'
            if test not in exclude and (len(include) == 0 or test in include):
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
            return [], []

        try:
            if series.is_pure_pull():
                ret = self._test_series_pull(tree, series, series_dir)
            else:
                ret = self._test_series_patches(tree, series, series_dir)
        finally:
            core.log_end_sec()

        return ret

    def _test_series_patches(self, tree, series, series_dir):
        if not tree.check_applies(series):
            series_apply = os.path.join(series_dir, "apply")
            os.makedirs(series_apply)

            already_applied = tree.check_already_applied(series)
            if already_applied:
                core.log("Series already applied", "")
                with open(os.path.join(series_apply, "retcode"), "w+") as fp:
                    fp.write("0")
                with open(os.path.join(series_apply, "desc"), "w+") as fp:
                    fp.write(f"Patch already applied to {tree.name}")
            else:
                core.log("Series does not apply", "")
                with open(os.path.join(series_apply, "retcode"), "w+") as fp:
                    fp.write("1")
                with open(os.path.join(series_apply, "desc"), "w+") as fp:
                    fp.write(f"Patch does not apply to {tree.name}")
            return [already_applied], [already_applied]

        series_ret = []
        patch_ret = []
        tree.reset()

        for test in self.series_tests:
            ret = test.exec(tree, series, series_dir)
            series_ret.append(ret)

        for patch in series.patches:
            core.log_open_sec("Testing patch " + patch.title)

            current_patch_ret = []

            patch_dir = os.path.join(series_dir, str(patch.id))
            if not os.path.exists(patch_dir):
                os.makedirs(patch_dir)

            try:
                tree.apply(patch)

                for test in self.patch_tests:
                    ret = test.exec(tree, patch, patch_dir)
                    current_patch_ret.append(ret)
            finally:
                core.log_end_sec()

            patch_ret.append(current_patch_ret)

        return series_ret, patch_ret

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
            return [], []

        patch = series.patches[0]
        current_patch_ret = []

        core.log_open_sec(f"Testing pull request {patch.title}")

        patch_dir = os.path.join(series_dir, str(patch.id))
        if not os.path.exists(patch_dir):
            os.makedirs(patch_dir)

        try:
            for test in self.patch_tests:
                if test.is_pull_compatible():
                    ret = test.exec(tree, patch, patch_dir)
                    current_patch_ret.append(ret)
        finally:
            core.log_end_sec()

        return [], [current_patch_ret]
