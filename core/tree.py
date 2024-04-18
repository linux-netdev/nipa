# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

""" The git tree module """

import multiprocessing
import os
import tempfile
from typing import List

import core
import core.cmd as CMD
from core import Patch


# TODO: add patch and CmdError as init here
class PatchApplyError(Exception):
    pass


class PullError(Exception):
    pass


class TreeNotClean(Exception):
    pass


class WorktreeNesting(Exception):
    pass


class Tree:
    """The git tree class

    Git tree class which controls a git tree
    """
    def __init__(self, name, pfx, fspath, remote=None, branch=None,
                 wt_id=None, parent=None):
        self.name = name
        self.pfx = pfx
        self.path = os.path.abspath(fspath)
        self.remote = remote
        self.branch = branch

        if parent:
            self.lock = parent.lock
        else:
            self.lock = multiprocessing.RLock()

        if remote and not branch:
            self.branch = remote + "/main"

        self._wt_id = wt_id
        self._saved_path = None

        self._check_tree()

    def work_tree(self, worker_id):
        # Create a worktree for the repo, returns new Tree object
        if self._wt_id:
            raise WorktreeNesting()

        name = f'wt-{worker_id}'
        new_path = os.path.join(self.path, name)
        if not os.path.exists(new_path):
            self.git(["worktree", "add", name])

        new_name = self.name + f'-{worker_id}'
        return Tree(new_name, self.pfx, new_path, self.remote, self.branch,
                    wt_id=worker_id, parent=self)

    def git(self, args: List[str]):
        self.lock.acquire(timeout=300)
        try:
            return CMD.cmd_run(["git"] + args, cwd=self.path)
        finally:
            self.lock.release()

    def git_am(self, patch):
        return self.git(["am", "-s", "--", patch])

    def git_pull(self, pull_url):
        cmd = ["pull", "--no-edit", "--signoff"]
        cmd += pull_url.split()
        return self.git(cmd)

    def git_push(self, remote, spec):
        cmd = ["push", remote, spec]
        return self.git(cmd)

    def git_status(self, untracked=None, short=False):
        cmd = ["status"]
        if short:
            cmd += ["-s"]
        if untracked is not None:
            cmd += ["-u", untracked]
        return self.git(cmd)

    def git_merge_base(self, c1, c2, is_ancestor=False):
        cmd = ["merge-base", c1, c2]
        if is_ancestor:
            cmd += ['--is-ancestor']
        return self.git(cmd)

    def git_fetch(self, remote):
        return self.git(['fetch', remote])

    def git_reset(self, target, hard=False):
        cmd = ['reset', target]
        if hard:
            cmd += ['--hard']
        return self.git(cmd)

    def git_find_patch(self, needle, depth=1000):
        cmd = [
            "log", "--pretty=format:'%h'", f"HEAD~{depth}..HEAD", f"--grep={needle}",
            "--fixed-strings"
        ]
        return self.git(cmd)

    def _check_tree(self):
        core.log_open_sec("Checking tree " + self.name)
        try:
            out = self.git_status(untracked="no", short=True)
            if out:
                raise TreeNotClean(f"Tree {self.name} is not clean")
        finally:
            core.log_end_sec()

    def head_hash(self):
        return self.git(['rev-parse', 'HEAD']).strip()

    def reset(self, fetch=None):
        core.log_open_sec("Reset tree " + self.name)
        try:
            if fetch or (fetch is None and self.remote):
                self.git_fetch(self.remote)
            self.git_reset(self.branch, hard=True)
        finally:
            core.log_end_sec()

    def remotes(self):
        """
        Returns a dict of dicts like {"origin": {"fetch": URL1, "push": URL2}}
        """
        cmd = ["remote", "-v"]
        ret = self.git(cmd)
        lines = ret.split('\n')
        result = {}
        for l in lines:
            if not l:
                continue
            bits = l.split()
            info = result.get(bits[0], {})
            info[bits[2][1:-1]] = bits[1]
            result[bits[0]] = info
        return result

    def contains(self, commit):
        core.log_open_sec("Checking for commit " + commit)
        try:
            self.git_merge_base(commit, 'HEAD', is_ancestor=True)
            ret = True
        except CMD.CmdError:
            ret = False
        finally:
            core.log_end_sec()

        return ret

    def _find_patch(self, patch):
        out = self.git_find_patch(patch.title)
        return out

    def is_applied(self, thing):
        ret = True

        if isinstance(thing, Patch):
            ret &= bool(self._find_patch(thing))
        elif hasattr(thing, "patches"):
            for patch in thing.patches:
                ret &= bool(self._find_patch(patch))

        return ret

    def check_already_applied(self, thing):
        core.log_open_sec("Checking if applied " + thing.title)
        try:
            self.reset()
            ret = self.is_applied(thing)
        finally:
            core.log_end_sec()

        return ret

    def _apply_patch_safe(self, patch):
        try:
            with tempfile.NamedTemporaryFile() as fp:
                patch.write_out(fp)
                core.log_open_sec("Applying patch " + patch.title)
                try:
                    self.git_am(fp.name)
                finally:
                    core.log_end_sec()
        except CMD.CmdError as e:
            try:
                self.git(["am", "--abort"])
            except CMD.CmdError:
                pass
            raise PatchApplyError(e) from e

    def apply(self, thing):
        if isinstance(thing, Patch):
            self._apply_patch_safe(thing)
        elif hasattr(thing, "patches"):
            for patch in thing.patches:
                self._apply_patch_safe(patch)
        else:
            raise Exception("Can't apply object '%s' to the git tree" % (type(thing), ))

    def check_applies(self, thing):
        core.log_open_sec("Test-applying " + thing.title)
        try:
            self.reset()
            self.apply(thing)
            ret = True
        except PatchApplyError:
            ret = False
        finally:
            core.log_end_sec()

        return ret

    def _pull_safe(self, pull_url):
        try:
            self.git_pull(pull_url)
        except CMD.CmdError as e:
            try:
                self.git(["merge", "--abort"])
            except CMD.CmdError:
                pass
            raise PullError(e) from e

    def pull(self, pull_url, reset=True):
        core.log_open_sec("Pulling " + pull_url)
        try:
            if reset:
                self.reset()
            self._pull_safe(pull_url)
        finally:
            core.log_end_sec()
