# SPDX-License-Identifier: GPL-2.0

"""Test execution engine with crash-safe progress tracking."""

import json
import os
import signal
import subprocess
import time

from lib.nipa import has_crash, extract_crash, namify


# Per-test wall-clock limit before we start tearing the test down (seconds).
TEST_TIMEOUT = 600
# How long to wait for a timed-out test to shut down gracefully after the
# first (SIGINT) signal before we force-kill its whole process tree.
GRACEFUL_KILL_WAIT = 60


def find_newest_test(tests_dir):
    """Scan tests_dir for the newest test subdirectory.

    Returns the newest one (by mtime) or None.
    """
    if not os.path.isdir(tests_dir):
        return None

    candidates = []
    for entry in os.listdir(tests_dir):
        full = os.path.join(tests_dir, entry)
        if not os.path.isdir(full):
            continue
        candidates.append((os.path.getmtime(full), full))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][1]


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



def load_filters(test_dir):
    """Load crash filters from filters.json in the test directory.

    Returns the filter dict, or None if no filters file.
    """
    path = os.path.join(test_dir, 'filters.json')
    if not os.path.exists(path):
        print(f"Warning: no filters file at {path}")
        return None
    try:
        with open(path, encoding='utf-8') as fp:
            return json.load(fp)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"Warning: failed to parse {path}: {e}")
        return None


def _has_real_crash(dmesg_text, filters):
    """Check dmesg for crashes. Returns True if crash is real (not ignored).

    Uses extract_crash to get fingerprints, then checks them against
    the ignore-crashes list in filters.
    """
    if not has_crash(dmesg_text):
        return False

    _crash_lines, finger_prints = extract_crash(
        dmesg_text, '', lambda: filters)

    if not finger_prints:
        # Crash detected but no fingerprints extracted — treat as real
        return True

    if filters and 'ignore-crashes' in filters:
        ignore = set(filters['ignore-crashes'])
        if not finger_prints - ignore:
            return False  # all fingerprints are ignored

    return True


def _signal_group(pgid, sig):
    """Send a signal to a process group, ignoring it if already gone."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def _kill_session(sid, sig):
    """Send a signal to every process in session ``sid`` (best effort).

    run_kselftest.sh wraps each test in nested ``timeout`` calls; the inner
    one (no --foreground) moves the test into its *own process group*, so a
    killpg() on the launcher's group misses it. SIGKILL also can't be
    forwarded down the ``timeout`` chain like SIGINT is. Nothing in the
    chain calls setsid() though, so the whole tree stays in one session --
    and the launcher (started with start_new_session=True) is its leader,
    so ``sid`` equals the launcher's pid. Killing the session reaches every
    descendant regardless of process-group games.
    """
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            if os.getsid(pid) == sid:
                os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def _run_one_test(test_dir, output_dir, target, prog):
    """Run a single test and save stdout/stderr. Returns (retcode, elapsed).

    On timeout the test is asked to shut down gracefully (SIGINT, which
    Python turns into KeyboardInterrupt so the test's cleanup / __exit__
    runs and tears down netns, interfaces, etc). Only if the process tree
    is still alive after GRACEFUL_KILL_WAIT do we force-kill it (SIGKILL).

    start_new_session=True puts run_kselftest.sh and everything it spawns
    into one session. SIGINT goes to the launcher's process group --
    run_kselftest.sh wraps the test in nested ``timeout`` calls that move
    it into a separate process group, but ``timeout`` forwards SIGINT down
    to it. SIGKILL can't be forwarded, so the hard fallback kills the whole
    session instead (see _kill_session). Either way the actual test process
    is reached -- otherwise it is orphaned and lingers, poisoning the retry
    and later crashing on its own.
    """
    t1 = time.monotonic()
    # pylint: disable=consider-using-with
    proc = subprocess.Popen(
        ['./run_kselftest.sh', '-t', f'{target}:{prog}'],
        cwd=test_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    # With start_new_session the child is its own group leader (pgid == pid).
    pgid = proc.pid
    stop_kind = None
    try:
        out, err = proc.communicate(timeout=TEST_TIMEOUT)
        retcode = proc.returncode
    except subprocess.TimeoutExpired:
        retcode = 1
        print(f"  {target}:{prog}: timed out after {TEST_TIMEOUT}s, "
              "sending SIGINT to process group")
        _signal_group(pgid, signal.SIGINT)
        try:
            # communicate() returns once the pipes close, i.e. once the
            # whole tree has exited -- a good proxy for "cleaned up".
            out, err = proc.communicate(timeout=GRACEFUL_KILL_WAIT)
            stop_kind = 'graceful stop'
            print(f"  {target}:{prog}: shut down cleanly after SIGINT")
        except subprocess.TimeoutExpired:
            stop_kind = 'hard stop'
            print(f"  {target}:{prog}: still alive after {GRACEFUL_KILL_WAIT}s, "
                  "force-killing session")
            # proc.pid is the session leader (start_new_session); it is
            # still un-reaped here, so the sid is valid.
            _kill_session(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()

    stdout = (out or b'').decode('utf-8', 'ignore')
    stderr = (err or b'').decode('utf-8', 'ignore')
    if stop_kind is not None:
        # Preserve whatever the test printed, but mark the timeout in
        # both streams so it's visible whichever one is inspected.
        marker = f'\nNIPA RUNNER TIMEOUT {TEST_TIMEOUT} sec ({stop_kind})\n'
        stdout += marker
        stderr += marker
    elif not stdout and not stderr:
        print(f"  {target}:{prog}: no output (rc={retcode})")
    t2 = time.monotonic()
    elapsed = round(t2 - t1, 1)

    with open(os.path.join(output_dir, 'stdout'), 'w', encoding='utf-8') as fp:
        fp.write(stdout)
    with open(os.path.join(output_dir, 'stderr'), 'w', encoding='utf-8') as fp:
        fp.write(stderr)

    return retcode, elapsed


def run_tests(test_dir, results_dir):
    """Execute kselftest in 'installed' form.

    For each test:
      1. Check dmesg for crash from previous test -- if found, stop
      2. Check if test_name is in .attempted -- if so, skip (crash recovery)
      3. Write test_name to .attempted + fsync (crash-safe bookkeeping)
      4. Run via: ./run_kselftest.sh -t <target>:<test>
      5. Capture stdout/stderr, save to results_dir/<dir_name>/
      6. Drain dmesg output produced during the test, save to dmesg file
      7. Save metadata (retcode, time, target, prog) to info file

    Returns True if a kernel crash was detected, False otherwise.
    """
    tests = _list_tests(test_dir)
    if not tests:
        print("No tests found")
        return False

    print(f"Found {len(tests)} tests")

    previously_attempted = set(load_attempted(test_dir))
    for test_name in previously_attempted:
        print(f"Skipping previously attempted (crashed): {test_name}")

    # Load crash filters (deployed by hwksft)
    filters = load_filters(test_dir)

    # Open dmesg once, drain boot messages
    dmesg = DmesgReader()
    boot_lines = dmesg.drain()
    if boot_lines:
        boot_path = os.path.join(results_dir, 'boot-dmesg')
        with open(boot_path, 'w', encoding='utf-8') as fp:
            fp.write(boot_lines)
        print(f"Saved {len(boot_lines.splitlines())} lines of boot dmesg")

    crashed = _has_real_crash(boot_lines, filters) if boot_lines else False
    if crashed:
        print("Kernel crash detected during boot")

    for test_idx, (target, prog) in enumerate(tests):
        test_name = f"{target}:{prog}"
        safe_name = namify(prog)
        dir_name = f"{test_idx}-{safe_name}"

        if test_name in previously_attempted:
            continue

        # Check for crash from a previous test before starting a new one
        if crashed:
            print(f"[{test_idx+1}/{len(tests)}] Skipping {test_name}: "
                  "crash detected in previous test")
            continue

        print(f"[{test_idx+1}/{len(tests)}] Running {test_name}")

        # Mark as attempted before execution
        mark_attempted(test_dir, test_name)

        # Create output directory
        test_results_dir = os.path.join(results_dir, dir_name)
        os.makedirs(test_results_dir, exist_ok=True)

        # Run the test
        retcode, elapsed = _run_one_test(test_dir, test_results_dir,
                                         target, prog)

        # Drain dmesg produced during this test
        test_dmesg = dmesg.drain()
        crash_fps = set()
        if test_dmesg:
            with open(os.path.join(test_results_dir, 'dmesg'), 'w',
                      encoding='utf-8') as fp:
                fp.write(test_dmesg)
            if _has_real_crash(test_dmesg, filters):
                crashed = True
                print(f"[{test_idx+1}/{len(tests)}] {test_name}: "
                      "kernel crash detected in dmesg")
            elif has_crash(test_dmesg):
                _lines, fps = extract_crash(test_dmesg, '', lambda: filters)
                print(f"[{test_idx+1}/{len(tests)}] {test_name}: "
                      f"kernel crash in dmesg (ignored: {', '.join(fps)})")
            # Always extract fingerprints for the info file
            if has_crash(test_dmesg):
                _lines, crash_fps = extract_crash(test_dmesg, '', lambda: filters)

        # Retry if the test failed and no crash
        retry_retcode = None
        if retcode not in (0, 4) and not crashed:
            print(f"[{test_idx+1}/{len(tests)}] Retrying {test_name}")
            retry_dir = os.path.join(results_dir, f'{dir_name}-retry')
            os.makedirs(retry_dir, exist_ok=True)
            retry_retcode, _retry_elapsed = _run_one_test(
                test_dir, retry_dir, target, prog)
            # Drain retry dmesg
            retry_dmesg = dmesg.drain()
            if retry_dmesg:
                with open(os.path.join(retry_dir, 'dmesg'), 'w',
                          encoding='utf-8') as fp:
                    fp.write(retry_dmesg)
                if _has_real_crash(retry_dmesg, filters):
                    crashed = True
                if has_crash(retry_dmesg):
                    _lines, rfps = extract_crash(retry_dmesg, '', lambda: filters)
                    crash_fps.update(rfps)
            print(f"[{test_idx+1}/{len(tests)}] {test_name}: "
                  f"retry rc={retry_retcode}")

        # Save metadata
        info = {'retcode': retcode, 'time': elapsed,
                'target': target, 'prog': prog}
        if retry_retcode is not None:
            info['retry_retcode'] = retry_retcode
        if crash_fps:
            info['crashes'] = list(crash_fps)
        with open(os.path.join(test_results_dir, 'info'), 'w', encoding='utf-8') as fp:
            json.dump(info, fp)
            fp.flush()
            os.fsync(fp.fileno())

        print(f"[{test_idx+1}/{len(tests)}] {test_name}: rc={retcode} ({elapsed}s)")

    dmesg.close()
    return crashed


class DmesgReader:
    """Non-blocking reader for /dev/kmsg.

    Opens /dev/kmsg once and provides a drain() method that returns
    all lines accumulated since the last drain.  Not threaded — the
    caller is expected to drain between tests.

    /dev/kmsg gives each opener its own read position.  It does not
    support seek(), so we just read from wherever the kernel puts us
    (typically the start of the ring buffer) and drain forward.
    """

    def __init__(self):
        self._fd = None
        try:
            # O_RDONLY | O_NONBLOCK so reads return EAGAIN instead of blocking
            self._fd = os.open('/dev/kmsg', os.O_RDONLY | os.O_NONBLOCK)
        except (PermissionError, FileNotFoundError, OSError) as e:
            print(f"DmesgReader: cannot open /dev/kmsg: {e}")

    def drain(self):
        """Read all available lines from /dev/kmsg.

        Returns accumulated text as a string, or '' if nothing new.
        """
        if self._fd is None:
            return ''

        lines = []
        while True:
            try:
                data = os.read(self._fd, 8192)
            except OSError:
                # EAGAIN = no more data available
                break
            if not data:
                break
            # kmsg format: "priority,sequence,timestamp,-;message\n"
            # Convert to dmesg-style: "[  timestamp] message"
            raw = data.decode('utf-8', 'ignore')
            for raw_line in raw.splitlines():
                parts = raw_line.split(';', 1)
                if len(parts) == 2:
                    header, msg = parts
                    fields = header.split(',')
                    if len(fields) >= 3:
                        # timestamp is in microseconds
                        try:
                            ts_us = int(fields[2])
                            ts_s = ts_us / 1_000_000
                            lines.append(f'[{ts_s:>12.6f}] {msg}\n')
                        except ValueError:
                            lines.append(f'{msg}\n')
                    else:
                        lines.append(f'{msg}\n')
                else:
                    lines.append(f'{raw_line}\n')

        return ''.join(lines)

    def close(self):
        """Close the /dev/kmsg fd."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
