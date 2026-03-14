# SPDX-License-Identifier: GPL-2.0

import json
import os
import tempfile
import unittest
from unittest import mock

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.runner import (find_newest_unseen, mark_all_seen, load_attempted,
                        mark_attempted, run_tests, DmesgReader, _namify)


class TestFindNewestUnseen(unittest.TestCase):
    def test_single_unseen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'test1')
            os.makedirs(test_dir)

            result = find_newest_unseen(tmpdir)
            self.assertEqual(result, test_dir)

    def test_all_seen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'test1')
            os.makedirs(test_dir)
            with open(os.path.join(test_dir, '.seen'), 'w') as fp:
                fp.write('')

            result = find_newest_unseen(tmpdir)
            self.assertIsNone(result)

    def test_multiple_unseen_picks_newest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dir1 = os.path.join(tmpdir, 'test1')
            dir2 = os.path.join(tmpdir, 'test2')
            os.makedirs(dir1)
            os.makedirs(dir2)

            # Make dir2 newer
            import time
            time.sleep(0.1)
            os.utime(dir2, None)

            result = find_newest_unseen(tmpdir)
            self.assertEqual(result, dir2)

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = find_newest_unseen(tmpdir)
            self.assertIsNone(result)

    def test_nonexistent_dir(self):
        result = find_newest_unseen('/nonexistent/path')
        self.assertIsNone(result)


class TestMarkAllSeen(unittest.TestCase):
    def test_marks_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ['test1', 'test2', 'test3']:
                os.makedirs(os.path.join(tmpdir, name))

            mark_all_seen(tmpdir)

            for name in ['test1', 'test2', 'test3']:
                self.assertTrue(
                    os.path.exists(os.path.join(tmpdir, name, '.seen'))
                )

    def test_already_seen_not_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, 'test1')
            os.makedirs(test_dir)
            with open(os.path.join(test_dir, '.seen'), 'w') as fp:
                fp.write('')

            # Should not raise
            mark_all_seen(tmpdir)


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
    @mock.patch('subprocess.run')
    def test_correct_kernel_runs(self, mock_run, mock_dmesg_cls, _mock_uname):
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg
        mock_run.return_value = mock.Mock(
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
    @mock.patch('subprocess.run')
    def test_version_suffix_match(self, mock_run, mock_dmesg_cls, _mock_uname):
        """uname has LOCALVERSION suffix (-dirty) — should match '6.12.0'."""
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg
        mock_run.return_value = mock.Mock(
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
        self.assertEqual(_namify('test_name'), 'test-name')

    def test_special_chars(self):
        self.assertEqual(_namify('test/name.sh'), 'test-name-sh')

    def test_trailing_dash(self):
        self.assertEqual(_namify('test/'), 'test')

    def test_empty(self):
        self.assertEqual(_namify(''), 'no-name')

    def test_none(self):
        self.assertEqual(_namify(None), 'no-name')


class TestRunTests(unittest.TestCase):
    def _read_info(self, results_dir, dir_name='0-test1-sh'):
        info_path = os.path.join(results_dir, dir_name, 'info')
        with open(info_path, encoding='utf-8') as fp:
            return json.load(fp)

    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.run')
    def test_single_test_pass(self, mock_run, mock_dmesg_cls):
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_run.return_value = mock.Mock(
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
    @mock.patch('subprocess.run')
    def test_single_test_fail(self, mock_run, mock_dmesg_cls):
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_run.return_value = mock.Mock(
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

    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.run')
    def test_test_skip(self, mock_run, mock_dmesg_cls):
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_run.return_value = mock.Mock(
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
    @mock.patch('subprocess.run')
    def test_output_saved(self, mock_run, mock_dmesg_cls):
        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_run.return_value = mock.Mock(
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
                with mock.patch('subprocess.run') as mock_run:
                    run_tests(test_dir, results_dir)

            # No output directory should have been created (test was skipped)
            self.assertEqual(os.listdir(results_dir), [])

            # subprocess.run should NOT have been called (test was skipped)
            mock_run.assert_not_called()

    @mock.patch('subprocess.run')
    def test_no_tests(self, mock_run):
        mock_run.return_value = mock.Mock(
            returncode=1, stdout=b'', stderr=b''
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            run_tests(tmpdir, tmpdir)
            # No output dirs should be created
            self.assertEqual(os.listdir(tmpdir), [])


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
    @mock.patch('subprocess.run')
    def test_full_run(self, mock_run, mock_dmesg_cls, mock_uname):
        mock_uname.return_value = mock.Mock(release='6.12.0')

        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_run.return_value = mock.Mock(
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

            # .seen should be created
            seen_path = os.path.join(test_dir, '.seen')
            self.assertTrue(os.path.exists(seen_path))

    @mock.patch('os.uname')
    @mock.patch('lib.runner.DmesgReader')
    @mock.patch('subprocess.run')
    def test_crash_recovery_resume(self, mock_run, mock_dmesg_cls, mock_uname):
        mock_uname.return_value = mock.Mock(release='6.12.0')

        mock_dmesg = mock.Mock()
        mock_dmesg.drain.return_value = ''
        mock_dmesg_cls.return_value = mock_dmesg

        mock_run.return_value = mock.Mock(
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
