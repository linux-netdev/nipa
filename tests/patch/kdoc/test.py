# SPDX-License-Identifier: GPL-2.0

""" Test if kernel-doc generates new warnings """

import collections
import dataclasses
import re
import subprocess
from typing import List, Optional, Tuple

def get_git_head(tree) -> str:
    """ Get the git commit ID for head commit. """

    cmd = ["git", "rev-parse", "HEAD"]
    result = subprocess.run(cmd, cwd=tree.path, capture_output=True, text=True,
                            check=True)

    return result.stdout.strip()

@dataclasses.dataclass(frozen=True, eq=True, order=True, init=True)
class KdocWarning:
    # The original warning message
    message : str = dataclasses.field(repr=False, compare=False)
    _ : dataclasses.KW_ONLY
    # Kind of warning line, determined during init
    kind : str = dataclasses.field(repr=True, compare=True)
    # The file path, or None if unable to determine
    file : Optional[str] = dataclasses.field(repr=True, compare=True)
    # The line, or None if unable to determine
    # Note: *not* part of comparison, or hash!
    line : Optional[int] = dataclasses.field(repr=True, compare=False)
    # The content of the warning (excluding kind, file, line)
    content : str = dataclasses.field(repr=True, compare=True)

    @classmethod
    def from_text(self, line, extra=None):
        message = line

        if extra:
            message += '\n' + extra

        parser = re.compile(
            r"""
            ^                         # Start of string
            (?P<kind>warning|error):  # Severity
            \s+                       # Spacing
            (?P<file>[/a-z0-9_.-]*):  # File path
            (?P<line>[0-9]+)          # Line number
            \s*                       # Spacing
            (?P<content>.*)           # Warning content
            $                         # End of string
            """,
            re.VERBOSE | re.IGNORECASE)

        m = parser.match(line)
        if m:
            kind = m['kind']
            file = m['file']
            line = int(m['line'])
            content = m['content']
            if extra:
                content += '\n' + extra
        else:
            kind = 'Unknown'
            file = None
            line = None
            content = message

        return KdocWarning(message, kind=kind, file=file, line=line,
                           content=content)

    def __str__(self):
        return self.message

def parse_warnings(lines) -> List[KdocWarning]:
    skip = False
    length = len(lines)

    warnings = []

    # Walk through lines and convert to warning objects
    for i, line in enumerate(lines):
        if skip:
            skip = False
            continue

        if line.endswith(':') and i + 1 < length:
            extra = lines[i + 1]
            skip = True
        else:
            extra = None

        warnings.append(KdocWarning.from_text(line, extra))

    return warnings

def run_kernel_doc(tree, commitish, files) -> List[KdocWarning]:
    """ Run ./scripts/kdoc on a given commit and capture its results. """

    cmd = ["git", "checkout", "-q", commitish]
    subprocess.run(cmd, cwd=tree.path, capture_output=False, check=True)

    cmd = ["./scripts/kernel-doc", "-Wall", "-none"] + files
    result = subprocess.run(cmd, cwd=tree.path, text=True, check=False,
                            stderr=subprocess.PIPE)

    lines = result.stderr.strip().split('\n')

    return parse_warnings(lines)

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

def kdoc(tree, patch, _result_dir) -> Tuple[int, str, str]:
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

    current_set = set(current_warnings)
    incumbent_set = set(incumbent_warnings)

    # This construction preserves ordering vs using set difference
    new_warnings = [x for x in current_warnings if x not in incumbent_set]
    rm_warnings = [x for x in incumbent_warnings if x not in current_set]

    incumbent_count = len(incumbent_warnings)
    current_count = len(current_warnings)
    new_count = len(new_warnings)
    rm_count = len(rm_warnings)

    desc = f'Errors and warnings before: {incumbent_count} This patch: {current_count}'
    if new_count:
        desc += f' New: {new_count}'
    if rm_count:
        desc += f' Removed: {rm_count}'
    log += ["", desc]

    if rm_count:
        log += ["", "Warnings removed:"]
        log.extend(map(str, rm_warnings))

        file_breakdown = collections.Counter((x.file for x in rm_warnings))

        log += ["Per-file breakdown:"]
        for f, count in file_breakdown.items():
            log += [f'{count:6} {f}']

    if new_count:
        ret = 1

        log += ["", "New warnings added:"]
        log.extend(map(str, new_warnings))

        file_breakdown = collections.Counter((x.file for x in new_warnings))

        log += ["Per-file breakdown:"]
        for f, count in file_breakdown.items():
            log += [f'{count:6} {f}']

    return ret, desc, "\n".join(log)
