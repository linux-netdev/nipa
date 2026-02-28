# SPDX-License-Identifier: GPL-2.0

""" Test if the MAINTAINERS file needs an update """

import os
import subprocess
from typing import Tuple

#
# Checking for needed new MAINTAINERS entries
#

new_file_ignore_pfx = [ 'Documentation/', 'tools/testing/']

def extract_files(series):
    """Extract paths of new files being added by the series."""

    new_files = set()
    mod_files = set()
    lines = []
    for patch in series.patches:
        lines += patch.raw_patch.split("\n")

    # Walk lines, skip last since it doesn't have next
    for i, line in enumerate(lines[:-1]):
        next_line = lines[i + 1]

        if not next_line.startswith("+++ b/"):
            continue
        file_path = next_line[6:]

        # .startswith() can take a while array of alternatives
        if file_path.startswith(tuple(new_file_ignore_pfx)):
            continue

        if line == "--- /dev/null":
            new_files.add(file_path)
        else:
            mod_files.add(file_path)

    # We're testing a series, same file may appear multiple times
    mod_files -= new_files
    return list(new_files), list(mod_files)


def count_files_for_maintainer_entry(tree, maintainer_entry):
    """Count how many files are covered by a specific maintainer entry."""
    patterns = []

    # Extract file patterns from the maintainer entry
    for line in maintainer_entry.split("\n"):
        if line.startswith("F:"):
            pattern = line[2:].strip()
            patterns.append(pattern)
    if not patterns:
        return 0

    # Count files matching these patterns
    total_files = 0
    for pattern in patterns:
        if pattern[-1] == '/':
            where = pattern
            what = '*'
        elif '/' in pattern:
            where = os.path.dirname(pattern)
            what = os.path.basename(pattern)
        else:
            where = "."
            what = pattern
        cmd = ["find", where, "-name", what, "-type", "f"]
        result = subprocess.run(cmd, cwd=tree.path, capture_output=True,
                                text=True, check=False)
        if result.returncode == 0:
            total_files += result.stdout.count("\n")

    return total_files


def get_maintainer_entry_for_file(tree, file_path):
    """Get the full MAINTAINERS entry for a specific file."""

    cmd = ["./scripts/get_maintainer.pl", "--sections", file_path]
    result = subprocess.run(cmd, cwd=tree.path, capture_output=True, text=True,
                            check=False)

    if result.returncode == 0:
        return result.stdout
    return ""


def check_maintainer_coverage(tree, new_files, out):
    """Check if new files should have an MAINTAINERS entry."""
    has_miss = False
    has_fail = False
    has_warn = False
    warnings = []

    # Ideal entry size is <50. But if someone is adding a Kconfig file,
    # chances are they should be a maintainer.
    pass_target = 75
    if 'Kconfig' in new_files:
        pass_target = 3

    for file_path in new_files:
        # The build files are sometimes outside of the directory covered
        # by the new MAINTAINERS entry
        if file_path.endswith(("/Makefile", "/Kconfig")):
            continue

        out.append("\nChecking coverage for a new file: " + file_path)

        maintainer_info = get_maintainer_entry_for_file(tree, file_path)

        # This should not happen, Linus catches all
        if not maintainer_info.strip():
            warnings.append(f"Failed to fetch MAINTAINERS for {file_path}")
            has_warn = True
            continue

        # Parse the maintainer sections
        sections = []
        current_section = []

        prev = ""
        for line in maintainer_info.split("\n"):
            if len(line) > 1 and line[1] == ':':
                if not current_section:
                    current_section = [prev]
                current_section.append(line)
            elif len(line) < 2:
                if current_section:
                    sections.append("\n".join(current_section))
                    current_section = []
            prev = line

        if current_section:
            sections.append("\n".join(current_section))

        # Check each maintainer section
        min_cnt = 999999
        for section in sections:
            name = section.split("\n")[0]
            # Count files for this maintainer entry
            file_count = count_files_for_maintainer_entry(tree, section)
            out.append(f"  Section {name} covers ~{file_count} files")

            if 0 < file_count < pass_target:
                out.append("PASS")
                break
            min_cnt = min(min_cnt, file_count)
        else:
            # Intel and nVidia drivers have 400+ files, just warn for these
            # sort of sizes. More files than 500 means we fell down to subsystem
            # level of entries.
            out.append(f" MIN {min_cnt}")
            has_miss = True
            if min_cnt < 500:
                has_warn = True
            else:
                has_fail = True

    if has_miss:
        warnings.append("Expecting a new MAINTAINERS entry")
    else:
        warnings.append("MAINTAINERS coverage looks sufficient")

    ret = 0
    if has_fail:
        ret = 1
    elif has_warn:
        ret = 250

    return ret, "; ".join(warnings)


def maintainers(tree, series, _result_dir) -> Tuple[int, str, str]:
    """ Main function / entry point """

    # Check for new files in the series
    new_files, mod_files = extract_files(series)

    ret = 0
    log = ["New files:"] + new_files + ["", "Modified files:"] + mod_files

    if not new_files:
        desc = "No new files, skip"
    else:
        ret, desc = check_maintainer_coverage(tree, new_files, log)

    return ret, desc, "\n".join(log)
