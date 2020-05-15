# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" The main CI module """

import os

import core
from core import Test


class TesterAlreadyTested(Exception):
    pass


class Tester(object):
    """The main Test running class

    Test runner class which can be fed series of patches to run tests on them.

    """

    def __init__(self, result_dir):
        core.log_open_sec("Tester init")
        self.result_dir = result_dir
        if not os.path.exists(self.result_dir):
            os.makedirs(self.result_dir)

        tests_dir = os.path.abspath(core.CORE_DIR + "../../tests")

        self.series_tests = self.load_tests(tests_dir, "series")
        self.patch_tests = self.load_tests(tests_dir, "patch")
        core.log_end_sec()

    def load_tests(self, tests_dir, name):
        core.log_open_sec(name.capitalize() + " tests")
        tests_subdir = os.path.join(tests_dir, name)
        tests = []
        for td in os.listdir(tests_subdir):
            tests.append(Test(os.path.join(tests_subdir, td), td))
        core.log_end_sec()

        return tests

    def test_series(self, tree, series):
        core.log_open_sec("Running tests in tree %s for %s" %
                          (tree.name, series.title))

        series_dir = os.path.join(self.result_dir, str(series.id))
        done_file = os.path.join(series_dir, ".tester_done")
        if not os.path.exists(series_dir):
            os.makedirs(series_dir)
        elif os.path.exists(done_file):
            raise TesterAlreadyTested

        if not tree.check_applies(series):
            already_applied = tree.check_already_applied(series)
            if already_applied:
                core.log("Series already applied", "")
                with open(os.path.join(series_dir, "summary"), "w+") as fp:
                    fp.write(f"Patch already applied to {tree.name}")
            else:
                core.log("Series does not apply", "")
                with open(os.path.join(series_dir, "summary"), "w+") as fp:
                    fp.write(f"Patch does not apply to {tree.name}")
            core.log_end_sec()
            return [already_applied], [already_applied]

        tree.enter()
        try:
            series_ret = []
            patch_ret = []

            tree.reset()

            for test in self.series_tests:
                ret = test.exec(tree, series, series_dir)
                series_ret.append(ret)

            for test in self.patch_tests:
                test.prep()

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
            tree.leave()
            core.log_end_sec()

        os.mknod(done_file)

        return series_ret, patch_ret
