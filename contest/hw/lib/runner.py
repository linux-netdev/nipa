# SPDX-License-Identifier: GPL-2.0

"""Test execution engine with crash-safe progress tracking."""

import json
import os
import re
import select
import subprocess
import threading
import time


def find_newest_unseen(tests_dir):
    """Scan tests_dir for subdirectories without .seen file.

    Returns the newest one (by mtime) or None.
    """
    if not os.path.isdir(tests_dir):
        return None

    candidates = []
    for entry in os.listdir(tests_dir):
        full = os.path.join(tests_dir, entry)
        if not os.path.isdir(full):
            continue
        if os.path.exists(os.path.join(full, '.seen')):
            continue
        candidates.append((os.path.getmtime(full), full))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


def mark_all_seen(tests_dir):
    """Touch .seen file in every subdirectory of tests_dir."""
    if not os.path.isdir(tests_dir):
        return

    for entry in os.listdir(tests_dir):
        full = os.path.join(tests_dir, entry)
        if not os.path.isdir(full):
            continue
        seen_path = os.path.join(full, '.seen')
        if not os.path.exists(seen_path):
            with open(seen_path, 'w', encoding='utf-8') as fp:
                fp.write('')


def load_attempted(test_dir):
    """Load .attempted file (JSON list of test names already tried).

    Returns empty list if file doesn't exist or is corrupt (fresh run).
    """
    path = os.path.join(test_dir, '.attempted')
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as fp:
                return json.load(fp)
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def mark_attempted(test_dir, test_name):
    """Append test_name to .attempted file atomically.

    Write to a temp file, fsync, then rename (atomic on POSIX
    same-filesystem). Called BEFORE starting each test so that if the
    kernel crashes, this test will be skipped on resume (not retried).
    """
    attempted = load_attempted(test_dir)
    attempted.append(test_name)
    path = os.path.join(test_dir, '.attempted')
    tmp_path = path + '.tmp'
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    with os.fdopen(fd, 'w') as fp:
        json.dump(attempted, fp)
        fp.flush()
        os.fsync(fp.fileno())
    os.rename(tmp_path, path)


def _list_tests(test_dir):
    """List available tests from the installed kselftest layout.

    Reads kselftest-list.txt if present, otherwise scans for
    run_kselftest.sh test listing.
    """
    list_file = os.path.join(test_dir, 'kselftest-list.txt')
    if os.path.exists(list_file):
        tests = []
        with open(list_file, encoding='utf-8') as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                # Format: target:test_name
                parts = line.split(':', 1)
                if len(parts) == 2:
                    tests.append((parts[0].strip(), parts[1].strip()))
                else:
                    tests.append(('unknown', parts[0].strip()))
        return tests

    # Fallback: run the listing command
    ret = subprocess.run(
        ['./run_kselftest.sh', '--list'],
        cwd=test_dir, capture_output=True, timeout=30, check=False
    )
    if ret.returncode != 0:
        return []

    tests = []
    for line in ret.stdout.decode('utf-8', 'ignore').strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split(':', 1)
        if len(parts) == 2:
            tests.append((parts[0].strip(), parts[1].strip()))
    return tests


def _namify(what):
    """Convert test name to a safe identifier."""
    if not what:
        return "no-name"
    name = re.sub(r'[^0-9a-zA-Z]+', '-', what)
    if name and name[-1] == '-':
        name = name[:-1]
    return name


def run_tests(test_dir, results_dir):
    """Execute kselftest in 'installed' form.

    Flow for each test:
      1. Check if test_name is in .attempted -- if so, skip (crash recovery)
      2. Write test_name to .attempted + fsync (crash-safe bookkeeping)
      3. Start DmesgMonitor
      4. Run via: ./run_kselftest.sh -t <target>/<test>
      5. Capture stdout/stderr, save to results_dir/<test_name>/
      6. Stop DmesgMonitor, collect crash fingerprints
      7. Parse KTAP output for results
      8. Append to results list

    Tests that were in .attempted from a previous run (crash recovery)
    are recorded as result='fail' with a note about the crash.
    """
    tests = _list_tests(test_dir)
    if not tests:
        print("No tests found")
        return []

    print(f"Found {len(tests)} tests")

    previously_attempted = set(load_attempted(test_dir))
    results = []

    # Mark previously attempted tests as crashed
    for test_name in previously_attempted:
        print(f"Skipping previously attempted (crashed): {test_name}")
        results.append({
            'test': _namify(test_name),
            'group': 'selftests-hw',
            'result': 'fail',
            'crashes': ['kernel crash during test (previous attempt)'],
        })

    for test_idx, (target, prog) in enumerate(tests):
        test_name = f"{target}:{prog}"
        safe_name = _namify(prog)
        dir_name = f"{test_idx}-{safe_name}"

        if test_name in previously_attempted:
            continue

        print(f"[{test_idx+1}/{len(tests)}] Running {test_name}")

        # Mark as attempted before execution
        mark_attempted(test_dir, test_name)

        # Start dmesg monitoring
        dmesg = DmesgMonitor()
        dmesg.start()

        # Create output directory
        test_results_dir = os.path.join(results_dir, dir_name)
        os.makedirs(test_results_dir, exist_ok=True)

        # Run the test
        t1 = time.monotonic()
        try:
            ret = subprocess.run(
                ['./run_kselftest.sh', '-t', f'{target}:{prog}'],
                cwd=test_dir,
                capture_output=True,
                timeout=600,
                check=False
            )
            retcode = ret.returncode
            stdout = ret.stdout.decode('utf-8', 'ignore')
            stderr = ret.stderr.decode('utf-8', 'ignore')
        except subprocess.TimeoutExpired:
            retcode = 1
            stdout = ''
            stderr = 'test timed out'
            print(f"[{test_idx+1}/{len(tests)}] {test_name}: timed out")
        t2 = time.monotonic()

        # Save output
        with open(os.path.join(test_results_dir, 'stdout'), 'w', encoding='utf-8') as fp:
            fp.write(stdout)
        with open(os.path.join(test_results_dir, 'stderr'), 'w', encoding='utf-8') as fp:
            fp.write(stderr)

        # Stop dmesg and check for crashes
        crash_lines = dmesg.stop()

        # Determine result
        result = 'pass'
        if retcode == 4:
            result = 'skip'
        elif retcode != 0:
            result = 'fail'

        # Check KTAP output for skip indicators
        if 'ok' not in stdout.lower() and result == 'pass':
            result = 'skip'

        outcome = {
            'test': safe_name,
            'group': f'selftests-{_namify(target)}',
            'result': result,
            'time': round(t2 - t1, 1),
        }
        if crash_lines:
            outcome['crashes'] = crash_lines
            outcome['result'] = 'fail'

        print(f"[{test_idx+1}/{len(tests)}] {test_name}: {outcome['result']} ({outcome['time']}s)")

        results.append(outcome)

    return results


class DmesgMonitor:
    """Background thread that reads /dev/kmsg during test execution.

    Detects kernel crashes by looking for RIP, Call Trace, etc.
    """
    def __init__(self):
        self._thread = None
        self._stop_event = threading.Event()
        self._crash_lines = []
        self._lock = threading.Lock()

    def start(self):
        """Start monitoring /dev/kmsg for crash traces."""
        self._crash_lines = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            fp = open('/dev/kmsg', 'r', encoding='utf-8', errors='ignore')
        except (PermissionError, FileNotFoundError):
            return

        # Seek to end
        try:
            fp.seek(0, 2)
        except OSError:
            pass

        while not self._stop_event.is_set():
            ready, _, _ = select.select([fp], [], [], 0.5)
            if not ready:
                continue
            try:
                line = fp.readline()
            except OSError:
                break
            if not line:
                continue

            if ('] RIP: ' in line or
                    '] Call Trace:' in line or
                    '] ref_tracker: ' in line or
                    'unreferenced object 0x' in line):
                with self._lock:
                    self._crash_lines.append(line.strip())

        fp.close()

    def stop(self):
        """Stop monitoring. Returns list of crash lines found."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            return list(self._crash_lines)
