# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" Test representation """
# TODO: document

import datetime
import importlib
import json
import os

import core
import core.cmd as CMD


class Test(object):
    """Test class

    """
    def __init__(self, path, name):
        self.path = path
        self.name = name

        core.log_open_sec("Test %s init" % (self.name, ))

        self._info_load()

        # Load dynamically the python func
        if "pymod" in self.info:
            test_group = os.path.basename(os.path.dirname(path))
            m = importlib.import_module("tests.%s.%s.%s" % (test_group, name, self.info["pymod"]))
            self._exec_pyfunc = getattr(m, self.info["pyfunc"])
        if "run" in self.info:
            # If the test to run is not a fully qualified path, add the
            # test directory to make it so.
            if self.info["run"][0][0] != '/':
                self.info["run"][0] = os.path.join(self.path, self.info["run"][0])
        core.log_end_sec()

    def _info_load(self):
        with open(os.path.join(self.path, 'info.json'), 'r') as fp:
            self.info = json.load(fp)
        core.log("Info file", json.dumps(self.info, indent=2))

    def is_disabled(self):
        return "disabled" in self.info and self.info["disabled"]

    def write_result(self, result_dir, retcode=0, out="", err="", desc=""):
        test_dir = os.path.join(result_dir, self.name)
        if not os.path.exists(test_dir):
            os.makedirs(test_dir)

        with open(os.path.join(test_dir, "retcode"), "w+") as fp:
            fp.write(str(retcode))
        if out:
            with open(os.path.join(test_dir, "stdout"), "w+") as fp:
                fp.write(out)
        if err:
            with open(os.path.join(test_dir, "stderr"), "w+") as fp:
                fp.write(err)
        if desc:
            if not desc.endswith('\n'):
                desc += '\n'
            with open(os.path.join(test_dir, "desc"), "w+") as fp:
                fp.write(desc)
        with open(os.path.join(test_dir, "summary"), "w+") as fp:
            fp.write("==========\n")
            if retcode == 0:
                fp.write("%s - OKAY\n" % (self.name, ))
            elif retcode == 250:
                fp.write("%s - WARNING\n" % (self.name, ))
            else:
                fp.write("%s - FAILED\n" % (self.name, ))
                fp.write("\n")
                if err.strip():
                    if err[:-1] != '\n':
                        err += '\n'
                    fp.write(err)
                elif out.strip():
                    if out[:-1] != '\n':
                        out += '\n'
                    fp.write(out)

    def exec(self, tree, thing, result_dir):
        if self.is_disabled():
            core.log(f"Skipping test {self.name} - disabled", "")
            return True

        core.log_open_sec(f"Running test {self.name}")

        test_dir = os.path.join(result_dir, self.name)
        if not os.path.exists(test_dir):
            os.makedirs(test_dir)

        retcode, out, err, desc = self._exec(tree, thing, result_dir)

        self.write_result(result_dir, retcode, out, err, desc)

        core.log_end_sec()

        return retcode == 0

    def _exec(self, tree, thing, result_dir):
        if "run" in self.info:
            return self._exec_run(tree, thing, result_dir)
        elif "pymod" in self.info:
            core.log("START", datetime.datetime.now().strftime("%H:%M:%S.%f"))
            ret, desc = self._exec_pyfunc(tree, thing, result_dir)
            core.log("END", datetime.datetime.now().strftime("%H:%M:%S.%f"))
            return ret, "", "", desc

    def _exec_run(self, tree, thing, result_dir):
        rfd, wfd = None, None
        retcode = 0
        try:
            rfd, wfd = os.pipe()

            out, err = CMD.cmd_run(self.info["run"], include_stderr=True, cwd=tree.path,
                                   pass_fds=[wfd], add_env={"DESC_FD": str(wfd)})
        except core.cmd.CmdError as e:
            retcode = e.retcode
            out = e.stdout
            err = e.stderr

        desc = ""
        if rfd is not None:
            os.close(wfd)
            read_file = os.fdopen(rfd)
            desc = read_file.read()
            read_file.close()

        return retcode, out, err, desc
