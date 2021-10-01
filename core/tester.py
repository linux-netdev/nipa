# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" The main CI module """

import configparser
import os
import threading

import core
from core import Test


def load_tests(tests_dir, name):
    core.log_open_sec(name.capitalize() + " tests")
    tests_subdir = os.path.join(tests_dir, name)
    tests = []
    for td in os.listdir(tests_subdir):
        tests.append(Test(os.path.join(tests_subdir, td), td))
    core.log_end_sec()

    return tests


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

        self.series_tests = []
        self.patch_tests = []

    def run(self) -> None:
        config = configparser.ConfigParser()
        config.read(['nipa.config', 'pw.config', 'tester.config'])

        core.log_init(config.get('log', 'type', fallback='org'),
                      config.get('log', 'file', fallback=os.path.join(core.NIPA_DIR,
                                                                      f"{self.tree.name}.org")))

        core.log_open_sec("Tester init")
        if not os.path.exists(self.result_dir):
            os.makedirs(self.result_dir)

        tests_dir = os.path.abspath(core.CORE_DIR + "../../tests")

        self.series_tests = load_tests(tests_dir, "series")
        self.patch_tests = load_tests(tests_dir, "patch")
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

            self.barrier.wait()

    def test_series(self, tree, series):
        write_tree_selection_result(self.result_dir, series, series.tree_selection_comment)
        self._test_series(tree, series)
        mark_done(self.result_dir, series)

    def _test_series(self, tree, series):
        core.log_open_sec("Running tests in tree %s for %s" %
                          (tree.name, series.title))

        series_dir = os.path.join(self.result_dir, str(series.id))
        if not os.path.exists(series_dir):
            os.makedirs(series_dir)
        elif os.path.exists(os.path.join(series_dir, ".tester_done")):
            core.log(f"Already tested in {series_dir}", "")
            core.log_end_sec()
            return [], []

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
            core.log_end_sec()
            return [already_applied], [already_applied]

        series_ret = []
        patch_ret = []
        try:
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
        finally:
            core.log_end_sec()

        return series_ret, patch_ret
