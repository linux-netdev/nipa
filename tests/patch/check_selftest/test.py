# SPDX-License-Identifier: GPL-2.0

""" Test Makefile, .gitignore and config format """

import os
import subprocess
from typing import Tuple


LOCAL_DIR = os.path.dirname(__file__)


def ret_merge(ret, nret):
    """ merge results """
    if ret[0] == 0 or nret[0] == 0:
        val = 0
    else:
        val = min(ret[0], nret[0])

    desc = ""
    if ret[1] and nret[1]:
        desc = ret[1] + "; " + nret[1]
    else:
        desc = ret[1] + nret[1]
    return (val, desc)


def check_new_files_makefile(tree, new_files, log):
    """ Make sure new files are listed in a Makefile, somewhere """

    ret = (0, "")
    cnt = 0

    for path in new_files:
        if path.endswith(('.sh', '.py')):
            needle = path
        elif path.endswith(('.c')):
            needle = path.split('.')[0]
        else:
            log.append("makefile inclusion check ignoring " + path)
            continue

        makefile = os.path.dirname(path) + "/Makefile"

        cmd = ["git", "grep", "--exit-code", needle, "---", makefile]
        result = subprocess.run(cmd, cwd=tree.path, check=False)
        log.append(" ".join(cmd) + f":: {result.returncode}")
        if result.returncode:
            ret_merge(ret, (1, path + " not found in Makefile"))
        cnt += 1

    if not ret[0] and cnt:
        ret = (0, f"New files in Makefile checked ({cnt})")

    return ret


def check_new_files_gitignore(tree, new_files, log):
    """ Make sure new binaries are listed in .gitignore """

    ret = (0, "")
    cnt = 0

    for path in new_files:
        if path.endswith(('.c')):
            needle = path.split('.')[0]
        else:
            log.append("gitignore check ignoring " + path)
            continue

        target = os.path.dirname(path) + "/.gitignore"

        cmd = ["git", "grep", "--exit-code", needle, "---", target]
        result = subprocess.run(cmd, cwd=tree.path, check=False)
        log.append(" ".join(cmd) + f":: {result.returncode}")
        if result.returncode:
            ret_merge(ret, (1, needle + " not found in .gitignore"))
        cnt += 1

    if not ret[0] and cnt:
        ret = (0, f"New files in gitignore checked ({cnt})")

    return ret


def _check_file_fmt(tree, path, script, result_dir, ident):
    cmd = [script, os.path.join(tree.path, path)]

    result = subprocess.run(cmd, cwd=LOCAL_DIR, capture_output=True,
                            text=True, check=False)
    with open(os.path.join(result_dir, ident), "w", encoding="utf-8") as fp:
        fp.write(result.stdout)
    return result.returncode


def check_file_formats(tree, file_list, log, result_dir):
    """ Validate sort order of all touched files """

    ret = (0, "")
    i = 0
    for path in file_list:
        if path.endswith("/config"):
            script = "validate_config_format.py"
            fmt = f"fmt-config-{i}"
        elif path.endswith("/.gitignore"):
            script = "validate_config_format.py"
            fmt = f"fmt-gitignore-{i}"
        elif path.endswith("/Makefile"):
            script = "validate_Makefile_format.py"
            fmt = f"fmt-makefile-{i}"
        else:
            log.append("format check ignoring " + path)
            continue

        if _check_file_fmt(tree, path, script, result_dir, fmt):
            ret = ret_merge(ret, (1, "Bad format: " + path))

    if not ret[0] and i:
        ret = (0, f"Good format ({i})")

    return ret


def extract_files(patch):
    """Extract paths of new files being added by the series."""

    new_files = set()
    mod_files = set()
    lines = patch.raw_patch.split("\n")

    # Walk lines, skip last since it doesn't have next
    for i, line in enumerate(lines[:-1]):
        next_line = lines[i + 1]

        if not next_line.startswith("+++ b/"):
            continue
        if 'tools/testing/selftests/' not in next_line:
            continue

        file_path = next_line[6:]

        if line == "--- /dev/null":
            new_files.add(file_path)
        else:
            mod_files.add(file_path)

    # We're testing a series, same file may appear multiple times
    mod_files -= new_files
    return list(new_files), list(mod_files)


def check_selftest(tree, patch, result_dir) -> Tuple[int, str, str]:
    """ Main function / entry point """

    # Check for new files in the series
    new_files, mod_files = extract_files(patch)

    ret = (0, "")
    log = ["New files:"] + new_files + ["", "Modified files:"] + mod_files + [""]

    if not new_files and not mod_files:
        ret = (0, "No changes to selftests")
    else:
        nret = check_file_formats(tree, new_files + mod_files, log, result_dir)
        ret = ret_merge(ret, nret)

        if new_files:
            nret = check_new_files_makefile(tree, new_files, log)
            ret = ret_merge(ret, nret)

            nret = check_new_files_gitignore(tree, new_files, log)
            ret = ret_merge(ret, nret)

        if not ret[0] and not ret[1]:
            ret = (0, f"New files {len(new_files)}, modified {len(mod_files)}, no checks")

    return ret[0], ret[1], "\n".join(log)
