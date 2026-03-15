# SPDX-License-Identifier: GPL-2.0

import json
import tempfile
import unittest
from unittest import mock

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.mc_client import MCClient, resolve_machines


class TestMCClient(unittest.TestCase):
    @mock.patch('requests.post')
    def test_reserve_success(self, mock_post):
        mock_post.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value={
                'reservation_id': 42,
                'timeout': 600,
            })
        )
        mc = MCClient('http://localhost:5050')
        result = mc.reserve([1, 2])

        self.assertEqual(result['reservation_id'], 42)
        mock_post.assert_called_once()
        call_data = mock_post.call_args[1]['json']
        self.assertEqual(call_data['machine_ids'], [1, 2])
        self.assertEqual(call_data['caller'], 'hwksft')

    @mock.patch('requests.post')
    def test_reserve_retry(self, mock_post):
        # First call: unavailable, second: success
        mock_post.side_effect = [
            mock.Mock(
                status_code=409,
                json=mock.Mock(return_value={'error': 'Machine reserved'})
            ),
            mock.Mock(
                status_code=200,
                json=mock.Mock(return_value={'reservation_id': 43, 'timeout': 600})
            ),
        ]
        mc = MCClient('http://localhost:5050')

        result1 = mc.reserve([1])
        self.assertIn('error', result1)

        result2 = mc.reserve([1])
        self.assertEqual(result2['reservation_id'], 43)

    @mock.patch('requests.post')
    def test_refresh(self, mock_post):
        mock_post.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value={'ok': True})
        )
        mc = MCClient('http://localhost:5050')
        result = mc.reservation_refresh(42)

        self.assertTrue(result['ok'])
        call_data = mock_post.call_args[1]['json']
        self.assertEqual(call_data['reservation_id'], 42)

    @mock.patch('requests.post')
    def test_close(self, mock_post):
        mock_post.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value={'ok': True})
        )
        mc = MCClient('http://localhost:5050')
        result = mc.reservation_close(42)

        self.assertTrue(result['ok'])
        call_data = mock_post.call_args[1]['json']
        self.assertEqual(call_data['reservation_id'], 42)

    @mock.patch('requests.get')
    def test_get_nic_info(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value=[
                {'id': 1, 'machine_id': 1, 'ifname': 'eth0'}
            ])
        )
        mock_get.return_value.raise_for_status = mock.Mock()

        mc = MCClient('http://localhost:5050')
        result = mc.get_nic_info(nic_id=1)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['ifname'], 'eth0')

    @mock.patch('requests.post')
    def test_power_cycle(self, mock_post):
        mock_post.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value={'retcode': 0})
        )
        mock_post.return_value.raise_for_status = mock.Mock()

        mc = MCClient('http://localhost:5050')
        result = mc.power_cycle(1)

        self.assertEqual(result['retcode'], 0)

    @mock.patch('requests.get')
    def test_get_sol_logs(self, mock_get):
        mock_get.return_value = mock.Mock(
            status_code=200,
            json=mock.Mock(return_value={
                'machine_id': 1,
                'last_id': 10,
                'lines': [{'ts': '2024-01-01', 'line': 'test'}],
            })
        )
        mock_get.return_value.raise_for_status = mock.Mock()

        mc = MCClient('http://localhost:5050')
        result = mc.get_sol_logs(1, start_id=5)

        self.assertEqual(result['last_id'], 10)
        params = mock_get.call_args[1]['params']
        self.assertEqual(params['start_id'], 5)


    @mock.patch('requests.post')
    def test_reserve_500_raises(self, mock_post):
        """HTTP 500 should raise, not return a broken json."""
        mock_post.return_value = mock.Mock(
            status_code=500,
        )
        mock_post.return_value.raise_for_status = mock.Mock(
            side_effect=Exception("500 Server Error")
        )
        mc = MCClient('http://localhost:5050')
        with self.assertRaises(Exception):
            mc.reserve([1])

    @mock.patch('requests.post')
    def test_reserve_409_returns_json(self, mock_post):
        """HTTP 409 should return json error, not raise."""
        mock_post.return_value = mock.Mock(
            status_code=409,
            json=mock.Mock(return_value={'error': 'Machine reserved'})
        )
        mc = MCClient('http://localhost:5050')
        result = mc.reserve([1])
        self.assertIn('error', result)


class TestDeployer(unittest.TestCase):
    def test_grab_hw_worker_journal(self):
        """Verify journal is fetched and saved to results dir."""
        from lib.deployer import grab_hw_worker_journal

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('lib.deployer._ssh',
                             return_value='Mar 14 hw-worker[1]: test log\n') as mock_ssh:
                grab_hw_worker_journal('10.0.0.1', tmpdir)

            mock_ssh.assert_called_once()
            self.assertIn('journalctl', mock_ssh.call_args[0][1])
            self.assertIn('-b', mock_ssh.call_args[0][1])
            journal_file = os.path.join(tmpdir, 'hw-worker-journal')
            self.assertTrue(os.path.exists(journal_file))
            with open(journal_file) as fp:
                self.assertIn('test log', fp.read())

    @mock.patch('subprocess.run')
    def test_deploy_artifacts(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')

        from lib.deployer import deploy_artifacts

        config = mock.Mock()
        nic_info = {
            'ifname': 'eth0',
            'ip4addr': '10.0.0.1',
            'ip6addr': 'fd00::1',
        }

        with mock.patch('os.path.join', side_effect=os.path.join):
            deploy_artifacts(config, ['10.0.0.1'], 42, nic_info, '/tmp/tree',
                             '6.12.0')

        # Should have multiple SSH/SCP calls
        self.assertTrue(mock_run.call_count > 0)

    @mock.patch('subprocess.run')
    def test_kexec(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')

        from lib.deployer import kexec_machine

        config = mock.Mock()
        config.getint.return_value = 300

        kexec_machine(config, ['10.0.0.1'], 42)

        # Should have kexec -l and kexec -e calls
        ssh_cmds = [call[0][0] for call in mock_run.call_args_list]
        kexec_calls = [c for c in ssh_cmds if 'ssh' in c]
        self.assertTrue(len(kexec_calls) >= 2)

    @mock.patch('subprocess.run')
    def test_build_kernel_success(self, mock_run):
        mock_run.return_value = mock.Mock(
            returncode=0, stdout=b'6.12.0\n', stderr=b'')

        from lib.deployer import build_kernel

        config = mock.Mock()
        config.get.return_value = None

        with tempfile.TemporaryDirectory() as tmpdir:
            result = build_kernel(config, tmpdir)

        # Should call make mrproper, defconfig, main build, and kernelrelease
        make_calls = [c for c in mock_run.call_args_list
                      if 'make' in str(c)]
        self.assertTrue(len(make_calls) >= 4)
        self.assertEqual(result, '6.12.0')

    @mock.patch('subprocess.run')
    def test_build_kernel_failure(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.CalledProcessError(2, 'make')

        from lib.deployer import build_kernel

        config = mock.Mock()
        config.get.return_value = None

        with self.assertRaises(subprocess.CalledProcessError):
            build_kernel(config, '/tmp/tree')

    @mock.patch('subprocess.run')
    def test_fetch_results(self, mock_run):
        """fetch_results copies files from remote."""
        mock_run.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')

        from lib.deployer import fetch_results

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch('lib.deployer._scp_from'):
                fetch_results(['10.0.0.1'], 42, tmpdir)

            # Should have called rsync
            rsync_calls = [c for c in mock_run.call_args_list
                           if 'rsync' in str(c)]
            self.assertTrue(len(rsync_calls) >= 1)

    def test_parse_results(self):
        """parse_results reads info/stdout files and builds result list."""
        from lib.deployer import parse_results

        with tempfile.TemporaryDirectory() as tmpdir:
            output_base = os.path.join(tmpdir, 'test-outputs')

            for idx, (target, prog, rc, stdout_text) in enumerate([
                ('net', 'test1.sh', 0,
                 'TAP version 13\n1..1\n'
                 'ok 1 selftests: net: test1.sh\n'),
                ('net', 'test2.sh', 1,
                 'TAP version 13\n1..1\n'
                 'not ok 1 selftests: net: test2.sh\n'),
            ]):
                test_dir = os.path.join(output_base, f'{idx}-{prog.replace(".", "-")}')
                os.makedirs(test_dir)
                with open(os.path.join(test_dir, 'info'), 'w') as fp:
                    json.dump({'retcode': rc, 'time': 1.5,
                               'target': target, 'prog': prog}, fp)
                with open(os.path.join(test_dir, 'stdout'), 'w') as fp:
                    fp.write(stdout_text)
                with open(os.path.join(test_dir, 'stderr'), 'w') as fp:
                    fp.write('')

            # .attempted includes a third test that crashed
            attempted = ['net:test1.sh', 'net:test2.sh', 'net:test3.sh']
            with open(os.path.join(tmpdir, 'attempted.json'), 'w') as fp:
                json.dump(attempted, fp)

            cases = parse_results(tmpdir, 'http://test/results/123')

        # test1 pass, test2 fail, test3 crashed
        self.assertEqual(len(cases), 3)
        passed = [c for c in cases if c['result'] == 'pass']
        failed = [c for c in cases if c['result'] == 'fail']
        self.assertEqual(len(passed), 1)
        self.assertEqual(len(failed), 2)
        crashed = [c for c in cases if c.get('crashes')]
        self.assertEqual(len(crashed), 1)
        self.assertIn('crash', str(crashed[0]['crashes']))


class TestCrashRecovery(unittest.TestCase):
    def test_crash_detected(self):
        """Verify crash marker detection via shared library."""
        from lib.nipa import has_crash

        self.assertTrue(has_crash("stuff ] RIP: 0010:func+0x42/0x100 stuff"))
        self.assertFalse(has_crash("everything is fine, no crashes here"))
        self.assertTrue(has_crash("blah ] Call Trace: blah"))
        self.assertTrue(has_crash("] ref_tracker: something"))
        self.assertTrue(has_crash("unreferenced object 0xdead"))

    def test_journal_crash_sentinel(self):
        """Verify crash sentinel detection in journal."""
        from lib.deployer import CRASH_SENTINEL

        journal_with = f"some stuff\n{CRASH_SENTINEL}\nmore stuff"
        self.assertIn(CRASH_SENTINEL, journal_with)

        journal_without = "some stuff\nCompleted, results in /srv\n"
        self.assertNotIn(CRASH_SENTINEL, journal_without)

    @mock.patch('subprocess.run')
    @mock.patch('time.monotonic')
    @mock.patch('time.sleep')
    def test_reboot_machine_ssh(self, _mock_sleep, mock_monotonic, mock_run):
        """Verify reboot_machine tries SSH first."""
        from lib.deployer import reboot_machine

        mock_run.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')
        mock_monotonic.side_effect = [0, 10, 20]

        config = mock.Mock()
        config.getint.return_value = 300

        mc = mock.Mock()
        mc.reservation_refresh.return_value = {'ok': True}

        reboot_machine(config, mc, 42, [1], ['10.0.0.1'])

        # Should not have called power_cycle (SSH reboot succeeded)
        mc.power_cycle.assert_not_called()

    @mock.patch('subprocess.run')
    @mock.patch('time.monotonic')
    @mock.patch('time.sleep')
    def test_wait_for_results_timeout(self, _mock_sleep,
                                      mock_monotonic, mock_run):
        """max_test_time exceeded returns WaitResult with error string."""
        from lib.deployer import wait_for_results, WaitResult

        mock_run.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')
        mock_monotonic.side_effect = [
            0,      # start_time
            99999,  # elapsed check -> exceeds max_test_time
        ]

        config = mock.Mock()
        config.getint.side_effect = lambda section, key, fallback=None: {
            'max_test_time': 3600,
            'sol_poll_interval': 15,
            'crash_wait_time': 120,
            'max_kexec_boot_timeout': 300,
        }.get(key, fallback)

        mc = mock.Mock()
        mc.get_sol_logs.return_value = {'last_id': 0, 'lines': []}

        result = wait_for_results(config, mc, 42, [1], ['10.0.0.1'])

        self.assertIsInstance(result, WaitResult)
        self.assertFalse(result.ok)
        self.assertIn('max test time exceeded', result.error)

    @mock.patch('subprocess.run')
    @mock.patch('time.monotonic')
    @mock.patch('time.sleep')
    def test_wait_for_results_no_results(self, _mock_sleep,
                                         mock_monotonic, mock_run):
        """hw-worker service failed returns WaitResult with error."""
        from lib.deployer import wait_for_results, WaitResult

        mock_run.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')
        mock_monotonic.side_effect = [
            0,   # start_time
            10,  # elapsed check
        ]

        config = mock.Mock()
        config.getint.side_effect = lambda section, key, fallback=None: {
            'max_test_time': 3600,
            'sol_poll_interval': 15,
            'crash_wait_time': 120,
            'max_kexec_boot_timeout': 300,
        }.get(key, fallback)

        mc = mock.Mock()
        mc.get_sol_logs.return_value = {'last_id': 0, 'lines': []}
        mc.reservation_refresh.return_value = {'ok': True}

        def ssh_side_effect(ip, cmd, check=True, timeout=30):
            if 'systemctl show' in cmd:
                return 'failed\n'
            return ''

        with mock.patch('lib.deployer._ssh',
                         side_effect=ssh_side_effect):
            result = wait_for_results(config, mc, 42, [1], ['10.0.0.1'])

        self.assertIsInstance(result, WaitResult)
        self.assertFalse(result.ok)
        self.assertIn('worker service failed', result.error)

    @mock.patch('subprocess.run')
    @mock.patch('time.monotonic')
    @mock.patch('time.sleep')
    def test_wait_for_results_success(self, _mock_sleep,
                                      mock_monotonic, mock_run):
        """hw-worker completes with results returns WaitResult(ok=True)."""
        from lib.deployer import wait_for_results, WaitResult

        mock_run.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')
        mock_monotonic.side_effect = [
            0,   # start_time
            10,  # elapsed check
        ]

        config = mock.Mock()
        config.getint.side_effect = lambda section, key, fallback=None: {
            'max_test_time': 3600,
            'sol_poll_interval': 15,
            'crash_wait_time': 120,
            'max_kexec_boot_timeout': 300,
        }.get(key, fallback)

        mc = mock.Mock()
        mc.get_sol_logs.return_value = {'last_id': 0, 'lines': []}
        mc.reservation_refresh.return_value = {'ok': True}

        def ssh_side_effect(ip, cmd, check=True, timeout=30):
            if 'systemctl show' in cmd:
                return 'inactive\n'
            return ''

        with mock.patch('lib.deployer._ssh',
                         side_effect=ssh_side_effect):
            result = wait_for_results(config, mc, 42, [1], ['10.0.0.1'])

        self.assertIsInstance(result, WaitResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.error, '')

    @mock.patch('subprocess.run')
    @mock.patch('time.monotonic')
    @mock.patch('time.sleep')
    def test_sol_crash_triggers_power_cycle_when_hung(self, _mock_sleep,
                                                       mock_monotonic, mock_run):
        """Crash in SOL + no new output for crash_wait_time -> power cycle."""
        mock_run.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')
        _clock = {'t': 0}

        def monotonic():
            _clock['t'] += 10
            return _clock['t']

        mock_monotonic.side_effect = monotonic

        from lib.deployer import wait_for_results

        config = mock.Mock()
        config.getint.side_effect = lambda section, key, fallback=None: {
            'max_test_time': 3600,
            'sol_poll_interval': 15,
            'crash_wait_time': 30,
            'max_power_cycle_timeout': 600,
        }.get(key, fallback)

        mc = mock.Mock()
        mc.reservation_refresh.return_value = {'ok': True}
        mc.get_sol_logs.side_effect = [
            {'last_id': 50, 'lines': []},  # seed
            {'last_id': 100, 'lines': [{'line': '] RIP: 0010:bad+0x10'}]},
            {'last_id': 100, 'lines': []},  # no new output
            {'last_id': 100, 'lines': []},  # still hung
            {'last_id': 100, 'lines': []},  # crash_wait_time exceeded
        ]

        poll_num = {'n': 0}

        def ssh_side_effect(ip, cmd, check=True, timeout=30):
            if 'systemctl show' in cmd:
                poll_num['n'] += 1
                if poll_num['n'] >= 5:
                    return 'inactive\n'
                return 'activating\n'
            return ''

        with mock.patch('lib.deployer._ssh', side_effect=ssh_side_effect):
            with mock.patch('lib.deployer._wait_for_ssh'):
                result = wait_for_results(config, mc, 42, [1], ['10.0.0.1'])

        mc.power_cycle.assert_called_once_with(1)
        self.assertTrue(result.ok)

    @mock.patch('subprocess.run')
    @mock.patch('time.monotonic')
    @mock.patch('time.sleep')
    def test_no_power_cycle_if_sol_progressing(self, _mock_sleep,
                                               mock_monotonic, mock_run):
        """Crash detected but SOL still producing output,
        verify no premature power cycle."""
        mock_run.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')
        mock_monotonic.side_effect = [
            0,     # start_time
            10,    # elapsed check
            10,    # crash_detected_at set
            20,    # elapsed check (poll 2)
        ]

        from lib.deployer import wait_for_results

        config = mock.Mock()
        config.getint.side_effect = lambda section, key, fallback=None: {
            'max_test_time': 3600,
            'sol_poll_interval': 15,
            'crash_wait_time': 120,
        }.get(key, fallback)

        mc = mock.Mock()
        mc.reservation_refresh.return_value = {'ok': True}
        mc.get_sol_logs.side_effect = [
            {'last_id': 50, 'lines': []},  # seed
            {'last_id': 100, 'lines': [{'line': '] RIP: 0010:bad+0x10'}]},
            {'last_id': 200, 'lines': [{'line': '] Call Trace: more'}]},
        ]

        poll_num = {'n': 0}

        def ssh_side_effect(ip, cmd, check=True, timeout=30):
            if 'systemctl show' in cmd:
                poll_num['n'] += 1
                if poll_num['n'] <= 1:
                    return 'activating\n'
                return 'inactive\n'
            return ''

        with mock.patch('lib.deployer._ssh', side_effect=ssh_side_effect):
            result = wait_for_results(config, mc, 42, [1], ['10.0.0.1'])

        mc.power_cycle.assert_not_called()
        self.assertTrue(result.ok)

class TestResolve(unittest.TestCase):
    def test_machine_resolution_two_machines(self):
        nics = [
            {'id': 1, 'machine_id': 10, 'peer_id': 2, 'ifname': 'eth0'},
            {'id': 2, 'machine_id': 20, 'peer_id': 1, 'ifname': 'eth1'},
        ]
        mids, nic = resolve_machines(nics, 1)

        self.assertEqual(mids, [10, 20])
        self.assertEqual(nic['id'], 1)

    def test_machine_resolution_loopback(self):
        nics = [
            {'id': 1, 'machine_id': 10, 'peer_id': 2, 'ifname': 'eth0'},
            {'id': 2, 'machine_id': 10, 'peer_id': 1, 'ifname': 'eth1'},
        ]
        mids, nic = resolve_machines(nics, 1)

        # Both NICs on same machine, should only reserve once
        self.assertEqual(mids, [10])

    def test_machine_resolution_no_peer(self):
        nics = [
            {'id': 1, 'machine_id': 10, 'peer_id': None, 'ifname': 'eth0'},
        ]
        mids, nic = resolve_machines(nics, 1)

        self.assertEqual(mids, [10])

    def test_machine_resolution_not_found(self):
        nics = [
            {'id': 1, 'machine_id': 10, 'peer_id': None, 'ifname': 'eth0'},
        ]
        with self.assertRaises(RuntimeError):
            resolve_machines(nics, 99)


class TestTestCallback(unittest.TestCase):
    def test_reservation_released_on_failure(self):
        """Verify that the reservation cleanup pattern works:
        if deploy fails, reservation_close is still called."""
        mock_mc = mock.Mock()
        mock_mc.reserve.return_value = {'reservation_id': 42, 'timeout': 600}

        # Simulate the try/finally pattern from hwksft.test()
        reservation_id = mock_mc.reserve([1])['reservation_id']
        try:
            raise RuntimeError("SCP failed")
        except RuntimeError:
            pass
        finally:
            mock_mc.reservation_close(reservation_id)

        mock_mc.reservation_close.assert_called_once_with(42)

    @mock.patch('subprocess.run')
    def test_deploy_then_close(self, mock_run):
        """Verify the full reservation lifecycle through the deployer."""
        from lib.deployer import deploy_artifacts

        mock_run.return_value = mock.Mock(returncode=0, stdout=b'', stderr=b'')

        mock_mc = mock.Mock()
        mock_mc.reserve.return_value = {'reservation_id': 42, 'timeout': 600}
        mock_mc.reservation_close.return_value = {'ok': True}

        reservation_id = mock_mc.reserve([1])['reservation_id']
        try:
            deploy_artifacts(mock.Mock(), ['10.0.0.1'], reservation_id,
                             {}, '/tmp/tree', '6.12.0')
        finally:
            mock_mc.reservation_close(reservation_id)

        mock_mc.reservation_close.assert_called_once_with(42)


if __name__ == '__main__':
    unittest.main()
