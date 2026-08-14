# SPDX-License-Identifier: GPL-2.0

import json
import os
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.runner import (find_newest_test, load_attempted,
                        mark_attempted, run_tests, DmesgReader,
                        DEFAULT_TEST_TIMEOUT,
                        _run_one_test, _known_bad_retry_decision)
from lib.nipa import namify


def _fake_popen(returncode=0, stdout=b'', stderr=b''):
    """Build a fake Popen whose communicate() yields the given output.

    _run_one_test now uses subprocess.Popen + communicate() instead of
    subprocess.run, so tests patch Popen and hand back one of these.
    """
    proc = mock.Mock()
    proc.pid = 4242
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


class TestFindNewestTest(unittest.TestCase):
    def test_single_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'test1')
            os.makedirs(test_dir)

            result = find_newest_test(tmpdir)
            self.assertEqual(result, test_dir)

    def test_multiple_picks_newest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dir1 = os.path.join(tmpdir, 'test1')
            dir2 = os.path.join(tmpdir, 'test2')
            os.makedirs(dir1)
            os.makedirs(dir2)

            # Make dir2 newer
            import time
            time.sleep(0.1)
            os.utime(dir2, None)

            result = find_newest_test(tmpdir)
            self.assertEqual(result, dir2)

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_newest_test(tmpdir)
            self.assertIsNone(result)

    def test_nonexistent_dir(self):
        result = find_newest_test('/nonexistent/path')
        self.assertIsNone(result)


class TestKernelVersionCheck(unittest.TestCase):
    @mock.patch('os.uname',
                return_value=mock.Mock(release='5.15.0-generic'))
    def test_wrong_kernel_exits(self, _mock_uname):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'test1')
            os.makedirs(test_dir)

            with open(os.path.join(test_dir, '.kernel-version'), 'w') as fp:
                fp.write('6.12.0\n')

            from hw_worker import main as hw_main
            with mock.patch('hw_worker.TESTS_DIR', tmpdir):
                with mock.patch('lib.runner.run_tests') as mock_rt:
                    hw_main()

            mock_rt.assert_not_called()

    @mock.patch('os.uname', return_value=mock.Mock(release='6.12.0'))
    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.Popen')
    def test_correct_kernel_runs(self, mock_popen, mock_dmesg_cls, _mock_uname):
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg
        mock_popen.return_value = _fake_popen(
            returncode=0, stdout=b'ok 1 test\n', stderr=b'')

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'test1')
            results_dir = os.path.join(tmpdir, 'results')
            os.makedirs(test_dir)
            os.makedirs(results_dir)

            with open(os.path.join(test_dir, '.kernel-version'), 'w') as fp:
                fp.write('6.12.0\n')
            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('net:test1.sh\n')

            from hw_worker import main as hw_main
            with mock.patch('hw_worker.TESTS_DIR', tmpdir):
                with mock.patch('hw_worker.RESULTS_DIR', results_dir):
                    hw_main()

            result_dir = os.path.join(results_dir, 'test1')
            # Check that test output was produced (info file in output dir)
            test_output = os.path.join(result_dir, '0-test1-sh')
            self.assertTrue(os.path.isdir(test_output))
            self.assertTrue(os.path.exists(os.path.join(test_output, 'info')))

    @mock.patch('os.uname', return_value=mock.Mock(release='6.12.0-dirty'))
    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.Popen')
    def test_version_suffix_match(self, mock_popen, mock_dmesg_cls, _mock_uname):
        """uname has LOCALVERSION suffix (-dirty) — should match '6.12.0'."""
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg
        mock_popen.return_value = _fake_popen(
            returncode=0, stdout=b'ok 1 test\n', stderr=b'')

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'test1')
            results_dir = os.path.join(tmpdir, 'results')
            os.makedirs(test_dir)
            os.makedirs(results_dir)

            with open(os.path.join(test_dir, '.kernel-version'), 'w') as fp:
                fp.write('6.12.0\n')
            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('net:test1.sh\n')

            from hw_worker import main as hw_main
            with mock.patch('hw_worker.TESTS_DIR', tmpdir):
                with mock.patch('hw_worker.RESULTS_DIR', results_dir):
                    hw_main()

            result_dir = os.path.join(results_dir, 'test1')
            test_output = os.path.join(result_dir, '0-test1-sh')
            self.assertTrue(os.path.isdir(test_output))

    @mock.patch('os.uname', return_value=mock.Mock(release='6.12.0-generic'))
    def test_version_prefix_overlap_rejected(self, _mock_uname):
        """'6.1' must NOT match '6.12.0-generic' — requires dash separator."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'test1')
            os.makedirs(test_dir)

            with open(os.path.join(test_dir, '.kernel-version'), 'w') as fp:
                fp.write('6.1\n')

            from hw_worker import main as hw_main
            with mock.patch('hw_worker.TESTS_DIR', tmpdir):
                with mock.patch('lib.runner.run_tests') as mock_rt:
                    hw_main()

            mock_rt.assert_not_called()

    def test_no_version_file_exits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'test1')
            os.makedirs(test_dir)

            from hw_worker import main as hw_main
            with mock.patch('hw_worker.TESTS_DIR', tmpdir):
                with mock.patch('lib.runner.run_tests') as mock_rt:
                    hw_main()

            mock_rt.assert_not_called()


class TestAttemptedTracking(unittest.TestCase):
    def test_mark_attempted_before_test(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mark_attempted(tmpdir, 'net/test1')

            attempted = load_attempted(tmpdir)
            self.assertEqual(attempted, ['net/test1'])

    def test_mark_attempted_appends(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mark_attempted(tmpdir, 'net/test1')
            mark_attempted(tmpdir, 'net/test2')

            attempted = load_attempted(tmpdir)
            self.assertEqual(attempted, ['net/test1', 'net/test2'])

    def test_fresh_run_no_attempted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            attempted = load_attempted(tmpdir)
            self.assertEqual(attempted, [])

    def test_mark_attempted_fsyncs(self):
        """Verify fsync is called (we check the file is written)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mark_attempted(tmpdir, 'net/test1')

            # File should exist and be readable
            path = os.path.join(tmpdir, '.attempted')
            self.assertTrue(os.path.exists(path))
            with open(path) as fp:
                data = json.load(fp)
            self.assertEqual(data, ['net/test1'])

    def test_mark_attempted_atomic(self):
        """Verify atomic write: .tmp file should not linger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mark_attempted(tmpdir, 'net/test1')

            # .tmp should not exist after successful write
            tmp_path = os.path.join(tmpdir, '.attempted.tmp')
            self.assertFalse(os.path.exists(tmp_path))

    def test_load_attempted_corrupt_json(self):
        """Corrupt .attempted file returns empty list instead of crashing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, '.attempted')
            with open(path, 'w') as fp:
                fp.write('not valid json{{{')

            attempted = load_attempted(tmpdir)
            self.assertEqual(attempted, [])

    def test_load_attempted_empty_file(self):
        """Empty .attempted file returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, '.attempted')
            with open(path, 'w') as fp:
                fp.write('')

            attempted = load_attempted(tmpdir)
            self.assertEqual(attempted, [])


class TestNamify(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(namify('test_name'), 'test-name')

    def test_special_chars(self):
        self.assertEqual(namify('test/name.sh'), 'test-name-sh')

    def test_trailing_dash(self):
        self.assertEqual(namify('test/'), 'test')

    def test_empty(self):
        self.assertEqual(namify(''), 'no-name')

    def test_none(self):
        self.assertEqual(namify(None), 'no-name')


class TestKnownBadRetryDecision(unittest.TestCase):
    OUTPUT = ('# TAP version 13\n'
              '# 1..3\n'
              '# not ok 1 - known failure\n'
              '# ok 2 - passing case\n'
              '# not ok 3 - another failure\n')

    def test_all_failed_subcases_known_bad(self):
        known_bad = {
            'selftests-drivers-net/test1-py': [
                'another-failure', 'known-failure',
            ],
        }
        skip, reason = _known_bad_retry_decision(
            self.OUTPUT, 'drivers/net', 'test1.py', known_bad)

        self.assertTrue(skip)
        self.assertEqual(
            reason, 'all 2 failed cases match known bad, skipping retry')

    def test_one_failed_subcase_unknown(self):
        known_bad = {
            'selftests-drivers-net/test1-py': ['known-failure'],
        }
        skip, reason = _known_bad_retry_decision(
            self.OUTPUT, 'drivers/net', 'test1.py', known_bad)

        self.assertFalse(skip)
        self.assertEqual(
            reason, 'case another-failure not found in known bad, retrying')

    def test_no_nested_failures_retries(self):
        skip, reason = _known_bad_retry_decision(
            'not ok 1 selftests: net: test1.sh\n',
            'net', 'test1.sh', {})

        self.assertFalse(skip)
        self.assertEqual(reason, 'no failed subcases found, retrying')

    def test_descriptionless_failure_retries(self):
        skip, reason = _known_bad_retry_decision(
            '# TAP version 13\n# 1..1\n# not ok 1\n',
            'net', 'test1.sh', {})

        self.assertFalse(skip)
        self.assertEqual(reason, 'no failed subcases found, retrying')

    def test_timeout_retries_even_when_failure_known(self):
        output = self.OUTPUT + 'NIPA RUNNER TIMEOUT 600 sec (hard stop)\n'
        known_bad = {
            'selftests-drivers-net/test1-py': [
                'another-failure', 'known-failure',
            ],
        }
        skip, reason = _known_bad_retry_decision(
            output, 'drivers/net', 'test1.py', known_bad)

        self.assertFalse(skip)
        self.assertEqual(reason, 'test timed out, retrying')


class TestRunTests(unittest.TestCase):
    def _read_info(self, results_dir, dir_name='0-test1-sh'):
        info_path = os.path.join(results_dir, dir_name, 'info')
        with open(info_path, encoding='utf-8') as fp:
            return json.load(fp)

    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.Popen')
    def test_single_test_pass(self, mock_popen, mock_dmesg_cls):
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_popen.return_value = _fake_popen(
            returncode=0,
            stdout=b'ok 1 test_name\n',
            stderr=b''
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'tests')
            results_dir = os.path.join(tmpdir, 'results')
            os.makedirs(test_dir)
            os.makedirs(results_dir)

            # Create kselftest-list.txt
            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('net:test1.sh\n')

            run_tests(test_dir, results_dir)

            info = self._read_info(results_dir)
            self.assertEqual(info['retcode'], 0)

    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.Popen')
    def test_single_test_fail(self, mock_popen, mock_dmesg_cls):
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_popen.return_value = _fake_popen(
            returncode=1,
            stdout=b'not ok 1 test_name\n',
            stderr=b''
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'tests')
            results_dir = os.path.join(tmpdir, 'results')
            os.makedirs(test_dir)
            os.makedirs(results_dir)

            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('net:test1.sh\n')

            run_tests(test_dir, results_dir)

            info = self._read_info(results_dir)
            self.assertEqual(info['retcode'], 1)

    @mock.patch('builtins.print')
    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.Popen')
    def test_known_bad_failures_skip_retry(self, mock_popen, mock_dmesg_cls,
                                           mock_print):
        mock_dmesg_cls.return_value.drain.return_value = ''
        mock_popen.return_value = _fake_popen(
            returncode=1,
            stdout=(b'# TAP version 13\n# 1..2\n'
                    b'# not ok 1 - known failure\n'
                    b'# ok 2 - passing case\n'))

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'tests')
            results_dir = os.path.join(tmpdir, 'results')
            os.makedirs(test_dir)
            os.makedirs(results_dir)
            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('drivers/net:test1.py\n')
            with open(os.path.join(test_dir, 'known-bad.json'), 'w') as fp:
                json.dump({'selftests-drivers-net/test1-py':
                           ['known-failure']}, fp)

            run_tests(test_dir, results_dir)

            self.assertEqual(mock_popen.call_count, 1)
            self.assertFalse(os.path.exists(os.path.join(
                results_dir, '0-test1-py-retry')))
            info = self._read_info(results_dir, '0-test1-py')
            self.assertNotIn('retry_retcode', info)

        messages = [str(call.args[0]) for call in mock_print.call_args_list]
        self.assertTrue(any('all 1 failed cases match known bad, '
                            'skipping retry' in msg for msg in messages))

    @mock.patch('builtins.print')
    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.Popen')
    def test_unknown_failure_is_retried(self, mock_popen, mock_dmesg_cls,
                                        mock_print):
        mock_dmesg_cls.return_value.drain.return_value = ''
        mock_popen.side_effect = [
            _fake_popen(
                returncode=1,
                stdout=(b'# TAP version 13\n# 1..2\n'
                        b'# not ok 1 - known failure\n'
                        b'# not ok 2 - new failure\n')),
            _fake_popen(returncode=0, stdout=b'ok 1 retry\n'),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'tests')
            results_dir = os.path.join(tmpdir, 'results')
            os.makedirs(test_dir)
            os.makedirs(results_dir)
            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('drivers/net:test1.py\n')
            with open(os.path.join(test_dir, 'known-bad.json'), 'w') as fp:
                json.dump({'selftests-drivers-net/test1-py':
                           ['known-failure']}, fp)

            run_tests(test_dir, results_dir)

            self.assertEqual(mock_popen.call_count, 2)
            info = self._read_info(results_dir, '0-test1-py')
            self.assertEqual(info['retry_retcode'], 0)

        messages = [str(call.args[0]) for call in mock_print.call_args_list]
        self.assertTrue(any('case new-failure not found in known bad, '
                            'retrying' in msg for msg in messages))

    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.Popen')
    def test_test_skip(self, mock_popen, mock_dmesg_cls):
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_popen.return_value = _fake_popen(
            returncode=4,
            stdout=b'',
            stderr=b''
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'tests')
            results_dir = os.path.join(tmpdir, 'results')
            os.makedirs(test_dir)
            os.makedirs(results_dir)

            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('net:test1.sh\n')

            run_tests(test_dir, results_dir)

            info = self._read_info(results_dir)
            self.assertEqual(info['retcode'], 4)

    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.Popen')
    def test_output_saved(self, mock_popen, mock_dmesg_cls):
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_popen.return_value = _fake_popen(
            returncode=0,
            stdout=b'ok 1 test output\n',
            stderr=b'some stderr\n'
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'tests')
            results_dir = os.path.join(tmpdir, 'results')
            os.makedirs(test_dir)
            os.makedirs(results_dir)

            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('net:test1.sh\n')

            run_tests(test_dir, results_dir)

            # Check output files exist — dir format is {idx}-{safe_name}
            test_output_dir = os.path.join(results_dir, '0-test1-sh')
            self.assertTrue(os.path.exists(os.path.join(test_output_dir, 'stdout')))
            self.assertTrue(os.path.exists(os.path.join(test_output_dir, 'stderr')))
            self.assertTrue(os.path.exists(os.path.join(test_output_dir, 'info')))

    def test_skips_previously_attempted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'tests')
            results_dir = os.path.join(tmpdir, 'results')
            os.makedirs(test_dir)
            os.makedirs(results_dir)

            # Pre-populate .attempted — format matches run_tests' test_name
            with open(os.path.join(test_dir, '.attempted'), 'w') as fp:
                json.dump(['net:test1.sh'], fp)

            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('net:test1.sh\n')

            with mock.patch('lib.runner.DmesgReader') as mock_dmesg_cls:
                mock_dmesg_cls.return_value.drain.return_value = ''
                with mock.patch('subprocess.Popen') as mock_popen:
                    run_tests(test_dir, results_dir)

            # No output directory should have been created (test was skipped)
            self.assertEqual(os.listdir(results_dir), [])

            # the test should NOT have been launched (it was skipped)
            mock_popen.assert_not_called()

    def test_no_tests(self):
        # An empty list means there is nothing to run; no test is launched.
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'tests')
            results_dir = os.path.join(tmpdir, 'results')
            os.makedirs(test_dir)
            os.makedirs(results_dir)
            open(os.path.join(test_dir, 'kselftest-list.txt'), 'w').close()

            run_tests(test_dir, results_dir)
            # No output dirs should be created
            self.assertEqual(os.listdir(results_dir), [])


class TestTestTimeoutConfig(unittest.TestCase):
    """NIPA_TEST_TIMEOUT from nic-test.env drives the per-test limit."""

    def test_from_env(self):
        from hw_worker import _test_timeout

        self.assertEqual(_test_timeout({'NIPA_TEST_TIMEOUT': '2400'}), 2400)

    def test_missing_falls_back(self):
        from hw_worker import _test_timeout

        self.assertEqual(_test_timeout({}), DEFAULT_TEST_TIMEOUT)

    def test_garbage_falls_back(self):
        from hw_worker import _test_timeout

        for bad in ('abc', '0', '-5', ''):
            self.assertEqual(_test_timeout({'NIPA_TEST_TIMEOUT': bad}),
                             DEFAULT_TEST_TIMEOUT)


class TestTimeoutHandling(unittest.TestCase):
    @mock.patch('lib.runner.os.killpg')
    @mock.patch('subprocess.Popen')
    def test_graceful_stop_after_sigint(self, mock_popen, mock_killpg):
        """On timeout we SIGINT the group; clean exit -> graceful stop."""
        proc = mock.Mock()
        proc.pid = 4242
        proc.returncode = 1
        # First communicate() times out; after SIGINT it returns the
        # partial output and the tree exits.
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd='run_kselftest.sh', timeout=600),
            (b'partial out\n', b'partial err\n'),
        ]
        mock_popen.return_value = proc

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = os.path.join(tmpdir, 'out')
            os.makedirs(out_dir)
            rc, _elapsed = _run_one_test(tmpdir, out_dir, 'net', 'test1.sh',
                                         600)
            with open(os.path.join(out_dir, 'stdout')) as fp:
                stdout = fp.read()
            with open(os.path.join(out_dir, 'stderr')) as fp:
                stderr = fp.read()

        self.assertEqual(rc, 1)
        # SIGINT to the process group, never escalated to SIGKILL
        mock_killpg.assert_called_once_with(4242, signal.SIGINT)

        # The caller's timeout is what we wait for, and what we report
        self.assertEqual(proc.communicate.call_args_list[0].kwargs['timeout'],
                         600)

        # Partial output preserved, marker appended to both streams
        self.assertIn('partial out', stdout)
        self.assertIn('partial err', stderr)
        self.assertIn('NIPA RUNNER TIMEOUT 600 sec', stdout)
        self.assertIn('graceful stop', stdout)
        self.assertIn('NIPA RUNNER TIMEOUT', stderr)
        self.assertIn('graceful stop', stderr)

    @mock.patch('lib.runner._kill_session')
    @mock.patch('lib.runner.os.killpg')
    @mock.patch('subprocess.Popen')
    def test_hard_stop_when_unresponsive(self, mock_popen, mock_killpg,
                                         mock_kill_session):
        """If SIGINT doesn't clean up in time we SIGKILL the session."""
        proc = mock.Mock()
        proc.pid = 99
        proc.returncode = 1
        # Times out on the run and again after SIGINT, then dies on SIGKILL.
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd='run_kselftest.sh', timeout=600),
            subprocess.TimeoutExpired(cmd='run_kselftest.sh', timeout=60),
            (b'', b''),
        ]
        mock_popen.return_value = proc

        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = os.path.join(tmpdir, 'out')
            os.makedirs(out_dir)
            rc, _elapsed = _run_one_test(tmpdir, out_dir, 'net', 'test1.sh',
                                         600)
            with open(os.path.join(out_dir, 'stderr')) as fp:
                stderr = fp.read()

        self.assertEqual(rc, 1)
        # SIGINT to the group first (graceful attempt) ...
        mock_killpg.assert_called_once_with(99, signal.SIGINT)
        # ... then escalate to SIGKILL of the whole session (killpg would
        # miss the test, which the kselftest harness puts in its own group).
        mock_kill_session.assert_called_once_with(99, signal.SIGKILL)

        self.assertIn('NIPA RUNNER TIMEOUT', stderr)
        self.assertIn('hard stop', stderr)


class TestDmesgReader(unittest.TestCase):
    def test_drain_no_fd(self):
        """DmesgReader with no fd returns empty string on drain."""
        dmesg = DmesgReader()
        # Force _fd to None (as if /dev/kmsg was not available)
        dmesg._fd = None
        self.assertEqual(dmesg.drain(), '')

    def test_close_no_fd(self):
        """Closing with no fd doesn't raise."""
        dmesg = DmesgReader()
        dmesg._fd = None
        dmesg.close()  # should not raise


class TestMainFlow(unittest.TestCase):
    def test_no_tests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Empty tests dir
            from hw_worker import main as hw_main
            with mock.patch('hw_worker.TESTS_DIR', tmpdir):
                hw_main()  # Should exit cleanly

    @mock.patch('os.uname')
    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.Popen')
    def test_full_run(self, mock_popen, mock_dmesg_cls, mock_uname):
        mock_uname.return_value = mock.Mock(release='6.12.0')

        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_popen.return_value = _fake_popen(
            returncode=0,
            stdout=b'ok 1 test\n',
            stderr=b''
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tests_dir = os.path.join(tmpdir, 'tests')
            results_dir = os.path.join(tmpdir, 'results')

            # Create test directory
            test_dir = os.path.join(tests_dir, '42')
            os.makedirs(test_dir)
            os.makedirs(results_dir)

            with open(os.path.join(test_dir, '.kernel-version'), 'w') as fp:
                fp.write('6.12.0\n')

            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('net:test1.sh\n')

            from hw_worker import main as hw_main
            with mock.patch('hw_worker.TESTS_DIR', tests_dir):
                with mock.patch('hw_worker.RESULTS_DIR', results_dir):
                    hw_main()

            # Results should have been written as output directories
            result_dir = os.path.join(results_dir, '42')
            test_output = os.path.join(result_dir, '0-test1-sh')
            self.assertTrue(os.path.isdir(test_output))
            self.assertTrue(os.path.exists(os.path.join(test_output, 'info')))
            self.assertTrue(os.path.exists(os.path.join(test_output, 'stdout')))

    @mock.patch('os.uname')
    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.Popen')
    def test_crash_recovery_resume(self, mock_popen, mock_dmesg_cls, mock_uname):
        mock_uname.return_value = mock.Mock(release='6.12.0')

        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_popen.return_value = _fake_popen(
            returncode=0,
            stdout=b'ok 1 test\n',
            stderr=b''
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tests_dir = os.path.join(tmpdir, 'tests')
            results_dir = os.path.join(tmpdir, 'results')

            test_dir = os.path.join(tests_dir, '42')
            os.makedirs(test_dir)
            os.makedirs(results_dir)

            with open(os.path.join(test_dir, '.kernel-version'), 'w') as fp:
                fp.write('6.12.0\n')

            # Pre-populate .attempted (simulating crash recovery)
            with open(os.path.join(test_dir, '.attempted'), 'w') as fp:
                json.dump(['net:test1.sh'], fp)

            with open(os.path.join(test_dir, 'kselftest-list.txt'), 'w') as fp:
                fp.write('net:test1.sh\nnet:test2.sh\n')

            from hw_worker import main as hw_main
            with mock.patch('hw_worker.TESTS_DIR', tests_dir):
                with mock.patch('hw_worker.RESULTS_DIR', results_dir):
                    hw_main()

            result_dir = os.path.join(results_dir, '42')

            # test1 was in .attempted, so it should be skipped (no output dir)
            # test2 should have run and produced output
            # test_idx=0 is test1, test_idx=1 is test2
            test2_dir = os.path.join(result_dir, '1-test2-sh')
            self.assertTrue(os.path.isdir(test2_dir))
            self.assertTrue(os.path.exists(os.path.join(test2_dir, 'info')))
            self.assertTrue(os.path.exists(os.path.join(test2_dir, 'stdout')))

            # test1 output dir should NOT exist (it was skipped)
            test1_dir = os.path.join(result_dir, '0-test1-sh')
            self.assertFalse(os.path.isdir(test1_dir))


if __name__ == '__main__':
    unittest.main()
