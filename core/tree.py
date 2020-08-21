# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" The git tree module """

import os
import re
import tempfile
import shlex

import core
import core.cmd as CMD
from core import Patch


def git(cmd):
    return CMD.cmd_run("git " + cmd)


def git_am(patch):
    return git("am -s " + patch)


def git_status(untracked=None, short=False):
    cmd = "status"
    if short:
        cmd += " -s"
    if untracked is not None:
        cmd += " -u" + untracked
    return git(cmd)


def git_merge_base(c1, c2, is_ancestor=False):
    cmd = f'merge-base {c1} {c2}'
    if is_ancestor:
        cmd += ' --is-ancestor'
    return git(cmd)


def git_fetch(remote):
    return git('fetch ' + remote)


def git_reset(target, hard=False):
    return git('reset {target} {hard}'.format(target=target,
                                              hard="--hard" if hard else ""))


def git_find_patch(needle, depth=1000):
    needle = re.escape(needle)
    needle = shlex.quote(needle)
    return git(f"log --pretty=format:'%h' HEAD~{depth}..HEAD --grep={needle}")


# TODO: add patch and CmdError as init here
class PatchApplyError(Exception):
    pass


class TreeNotClean(Exception):
    pass


class Tree:
    """The git tree class

    Git tree class which controls a git tree
    """

    def __init__(self, name, pfx, fspath, remote=None, branch=None):
        self.name = name
        self.pfx = pfx
        self.path = os.path.abspath(fspath)
        self.remote = remote
        self.branch = branch

        if remote and not branch:
            self.branch = remote + "/master"

        self._saved_path = None

        self._check_tree()

    def _check_tree(self):
        core.log_open_sec("Checking tree " + self.name)
        self.enter()
        try:
            out = git_status(untracked="no", short=True)
            if out:
                raise TreeNotClean(f"Tree {self.name} is not clean")
        finally:
            self.leave()
            core.log_end_sec()

    def _check_active(self):
        if not self._saved_path:
            raise Exception("Performing an action while not inside a git tree")
        if os.getcwd() != self.path:
            raise Exception("Git tree location was broken",
                            os.getcwd(), self.path)

    def enter(self):
        if os.getcwd() == self.path:
            raise Exception("Re-entering the same tree multiple times")
        self._saved_path = os.getcwd()
        os.chdir(self.path)

    def leave(self):
        self._check_active()
        os.chdir(self._saved_path)
        self._saved_path = None

    def reset(self, fetch=None):
        core.log_open_sec("Reset tree " + self.name)
        try:
            self._check_active()
            if fetch or (fetch is None and self.remote):
                git_fetch(self.remote)
            git_reset(self.branch, hard=True)
        finally:
            core.log_end_sec()

    def contains(self, commit):
        core.log_open_sec("Checking for commit " + commit)
        self.enter()
        try:
            git_merge_base(commit, 'HEAD', is_ancestor=True)
            ret = True
        except CMD.CmdError:
            ret = False
        finally:
            self.leave()
            core.log_end_sec()

        return ret

    @staticmethod
    def _find_patch(patch):
        out = git_find_patch(patch.title)
        return out

    def is_applied(self, thing):
        ret = True

        self._check_active()
        if isinstance(thing, Patch):
            ret &= bool(self._find_patch(thing))
        elif hasattr(thing, "patches"):
            for patch in thing.patches:
                ret &= bool(self._find_patch(patch))

        return ret

    def check_already_applied(self, thing):
        core.log_open_sec("Checking if applied " + thing.title)
        self.enter()
        try:
            self.reset()
            ret = self.is_applied(thing)
        finally:
            self.leave()
            core.log_end_sec()

        return ret

    @staticmethod
    def _apply_patch_safe(patch):
        try:
            with tempfile.NamedTemporaryFile() as fp:
                patch.write_out(fp)
                core.log_open_sec("Applying patch " + patch.title)
                try:
                    git_am(fp.name)
                finally:
                    core.log_end_sec()
        except CMD.CmdError as e:
            try:
                git("am --abort")
            except CMD.CmdError:
                pass
            raise PatchApplyError(e) from e

    def apply(self, thing):
        self._check_active()
        if isinstance(thing, Patch):
            self._apply_patch_safe(thing)
        elif hasattr(thing, "patches"):
            for patch in thing.patches:
                self._apply_patch_safe(patch)
        else:
            raise Exception("Can't apply object '%s' to the git tree" %
                            (type(thing),))

    def check_applies(self, thing):
        core.log_open_sec("Test-applying " + thing.title)
        self.enter()
        try:
            self.reset()
            self.apply(thing)
            ret = True
        except PatchApplyError:
            ret = False
        finally:
            self.leave()
            core.log_end_sec()

        return ret
