# SPDX-License-Identifier: GPL-2.0

""" Test if kernel-doc generates new warnings """

import os
import subprocess
from typing import List, Optional, Tuple

def get_git_head(tree) -> str:
    """ Get the git commit ID for head commit. """

    cmd = ["git", "rev-parse", "HEAD"]
    result = subprocess.run(cmd, cwd=tree.path, capture_output=True, text=True,
                            check=True)

    return result.stdout.strip()

def run_kernel_doc(tree, commitish, files):
    """ Run ./scripts/kdoc on a given commit and capture its results. """

    cmd = ["git", "checkout", "-q", commitish]
    subprocess.run(cmd, cwd=tree.path, capture_output=False, check=True)

    cmd = ["./scripts/kernel-doc", "-Wall", "-none"] + files
    result = subprocess.run(cmd, cwd=tree.path, text=True, check=False,
                            stderr=subprocess.PIPE)

    return result.stderr.strip().split('\n')

def extract_files(patch):
    """Extract paths added or modified by the patch."""

    all_files = set()
    mod_files = set()
    lines = patch.raw_patch.split("\n")

    # Walk lines, skip last since it doesn't have next
    for i, line in enumerate(lines[:-1]):
        next_line = lines[i + 1]

        if not next_line.startswith("+++ b/"):
            continue

        file_path = next_line[6:]

        all_files.add(file_path)

        if line != "--- /dev/null":
            mod_files.add(file_path)

    return list(mod_files), list(all_files)

def kdoc(tree, patch, result_dir) -> Tuple[int, str, str]:
    """ Main function / entry point """

    mod_files, all_files = extract_files(patch)

    if not mod_files or not all_files:
        return 1, "Patch has no modified files?", ""

    ret = 0
    desc = ""
    log = []

    head_commit = get_git_head(tree)

    try:
        incumbent_warnings = run_kernel_doc(tree, "HEAD~", mod_files)
        log += ["Warnings before patch:"]
        log.extend(map(str, incumbent_warnings))

        current_warnings = run_kernel_doc(tree, head_commit, all_files)
        log += ["", "Current warnings:"]
        log.extend(map(str, current_warnings))
    except subprocess.CalledProcessError as e:
        desc = f'{e.cmd} failed with exit code {e.returncode}'
        if e.stderr:
            log += e.stderr.split('\n')
        ret = 1

        return ret, desc, "\n".join(log)

    incumbent_count = len(incumbent_warnings)
    current_count = len(current_warnings)

    desc = f'Errors and warnings before: {incumbent_count} This patch: {current_count}'
    log += ["", desc]

    if current_count > incumbent_count:
        ret = 1

    return ret, desc, "\n".join(log)
