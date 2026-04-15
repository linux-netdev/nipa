# SPDX-License-Identifier: GPL-2.0

"""Shared result parsing helpers for kselftest output.

Functions in this module are used by both vmksft-p (VM testing) and
hwksft (HW testing) to interpret test output.
"""

import re


def guess_indicators(output):
    """Scan test output for pass/fail/skip indicator strings.

    Returns a dict with 'pass', 'fail', 'skip' boolean values.
    """
    return {
        "fail": output.find("[FAIL]") != -1 or output.find("[fail]") != -1 or
                output.find(" FAIL:") != -1 or
                bool(re.search(r"\nnot ok \d+( \d+)? selftests: ", output)) or
                output.find("\n# not ok 1") != -1,
        "skip": output.find("[SKIP]") != -1 or output.find("[skip]") != -1 or
                output.find(" # SKIP") != -1 or output.find("SKIP:") != -1,
        "pass": output.find("[OKAY]") != -1 or output.find("[PASS]") != -1 or
                output.find("[ OK ]") != -1 or output.find("[OK]") != -1 or
                output.find("[ ok ]") != -1 or output.find("[pass]") != -1 or
                output.find("PASSED all ") != -1 or
                bool(re.search(r"\nok \d+( \d+)? selftests: ", output)) or
                bool(re.search(
                    r"# Totals: pass:[1-9]\d* fail:0 (xfail:0 )?(xpass:0 )?skip:0 error:0",
                    output)),
    }


def result_from_indicators(retcode, indicators):
    """Determine test result from return code and output indicators."""
    result = 'pass'
    if indicators["skip"] or not indicators["pass"]:
        result = 'skip'
    if retcode == 4:
        result = 'skip'
    elif retcode:
        result = 'fail'
    if indicators["fail"]:
        result = 'fail'
    return result


def parse_nested_tests(full_run, namify_fn, prev_results=None):
    """Parse nested KTAP subtests from test output.

    Args:
        full_run: full stdout of the test run
        namify_fn: function to sanitize test names
        prev_results: if not None, this is a retry run — merge results
                      into prev_results instead of creating new entries

    Returns a list of subtest dicts (empty if prev_results is used,
    since results are merged in-place).
    """
    tests = []
    nested_tests = False

    result_re = re.compile(
        r"(not )?ok (\d+)( \d+)?( -)? ([^#]*[^ ])( +# +)?([^ ].*)?$")
    time_re = re.compile(r"time=(\d+)ms")

    for line in full_run.split('\n'):
        # nested subtests support: we parse the comments from 'TAP version'
        if nested_tests:
            if line.startswith("# "):
                line = line[2:]
            else:
                nested_tests = False
        elif line.startswith("# TAP version "):
            nested_tests = True
            continue

        if not nested_tests:
            continue

        if line.startswith("ok "):
            result = "pass"
        elif line.startswith("not ok "):
            result = "fail"
        else:
            continue

        v = result_re.match(line).groups()
        r = {'test': namify_fn(v[4])}

        if len(v) > 6 and v[5] and v[6]:
            if v[6].lower().startswith('skip'):
                result = "skip"

            t = time_re.findall(v[6].lower())
            if t:
                r['time'] = round(int(t[-1]) / 1000.)  # take the last one

        r['result'] = result

        if prev_results is not None:
            for entry in prev_results:
                if entry['test'] == r['test']:
                    entry['retry'] = result
                    break
            else:
                # the first run didn't validate this test: add it to the list
                r['result'] = 'skip'
                r['retry'] = result
                prev_results.append(r)
        else:
            tests.append(r)

    # return an empty list when there are prev results: no replacement needed
    return tests
