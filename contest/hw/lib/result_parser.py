# SPDX-License-Identifier: GPL-2.0

"""Parse HW worker artifacts without requiring the NIPA service stack."""

import json
import os

from .nipa import guess_indicators, namify, parse_nested_tests, \
    result_from_indicators


def parse_results(results_path, link):
    """Parse fetched test output into a vmksft-p-style result list.

    Reads info/stdout files from the test-outputs directory and
    the .attempted file to identify crashed tests.
    """
    output_dir = os.path.join(results_path, 'test-outputs')

    # Output dirs with no 'info' belong either to a retry or to a test which
    # never got to write one because the machine died under it.  Remember the
    # latter by test name, the .attempted pass below has no other way of
    # finding whatever output did make it to disk.  Names are only unique
    # within a target, so drop the ones we'd have to guess about.
    orphan_links = {}

    # Parse each test output directory
    cases = []
    completed_tests = set()
    if os.path.isdir(output_dir):
        for entry in sorted(os.listdir(output_dir)):
            test_dir = os.path.join(output_dir, entry)
            if not os.path.isdir(test_dir):
                continue

            info_path = os.path.join(test_dir, 'info')
            stdout_path = os.path.join(test_dir, 'stdout')
            entry_link = f'{link}/test-outputs/{entry}/'

            if not os.path.exists(info_path):
                if not entry.endswith('-retry'):
                    # hw_worker names the dirs "$index-$namify(prog)"
                    name = entry.split('-', 1)[-1]
                    if name in orphan_links:
                        orphan_links[name] = None
                    else:
                        orphan_links[name] = entry_link
                continue

            try:
                with open(info_path, encoding='utf-8') as fp:
                    info = json.load(fp)
            except (json.JSONDecodeError, ValueError):
                print(f"parse_results: bad JSON in {info_path}, marking as fail")
                cases.append({
                    'test': entry,
                    'group': 'selftests-hw',
                    'result': 'fail',
                    'link': entry_link,
                })
                continue

            retcode = info.get('retcode', 1)
            target = info.get('target', 'unknown')
            prog = info.get('prog', entry)
            test_name = f"{target}:{prog}"
            completed_tests.add(test_name)

            stdout = ''
            if os.path.exists(stdout_path):
                with open(stdout_path, encoding='utf-8') as fp:
                    stdout = fp.read()

            # Determine result using indicators
            indicators = guess_indicators(stdout)
            result = result_from_indicators(retcode, indicators)

            # Parse nested subtests
            nested = parse_nested_tests(stdout, namify)

            # Determine retry result if present
            retry_result = None
            retry_nested = None
            if 'retry_retcode' in info:
                retry_stdout = ''
                retry_dir = os.path.join(output_dir, entry + '-retry')
                retry_stdout_path = os.path.join(retry_dir, 'stdout')
                if os.path.exists(retry_stdout_path):
                    with open(retry_stdout_path, encoding='utf-8') as fp:
                        retry_stdout = fp.read()
                retry_indicators = guess_indicators(retry_stdout)
                retry_result = result_from_indicators(info['retry_retcode'],
                                                      retry_indicators)
                if nested:
                    retry_nested = list(nested)
                    parse_nested_tests(retry_stdout, namify,
                                       prev_results=retry_nested)

            safe_name = namify(prog)

            outcome = {
                'test': safe_name or entry,
                'group': f'selftests-{namify(target)}',
                'result': result,
                'link': entry_link,
            }
            if 'time' in info:
                outcome['time'] = info['time']
            if retry_result is not None:
                outcome['retry'] = retry_result
            if info.get('crashes'):
                outcome['crashes'] = info['crashes']
            if info.get('warnings'):
                outcome['warnings'] = info['warnings']
            if retry_nested:
                outcome['results'] = retry_nested
            elif nested:
                outcome['results'] = nested
            cases.append(outcome)

    # Check .attempted for crashed tests (attempted but no output)
    attempted_path = os.path.join(results_path, 'attempted.json')
    if os.path.exists(attempted_path):
        with open(attempted_path, encoding='utf-8') as fp:
            try:
                attempted = json.load(fp)
            except (json.JSONDecodeError, ValueError):
                attempted = []

        for test_name in attempted:
            if test_name not in completed_tests:
                prog = test_name.split(':', 1)[-1]
                cases.append({
                    'test': test_name,
                    'group': 'selftests-hw',
                    'result': 'fail',
                    'link': orphan_links.get(namify(prog)) or link,
                    'crashes': ['kernel crash during test'],
                })

    return cases
