# SPDX-License-Identifier: GPL-2.0

import datetime
import threading
import unittest
from unittest import mock

import sys
import os

# Add hw directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.bmc import BMC
from lib.health import HealthChecker, MachineState
from lib.sol_listener import SOLCollector as SOLListener
from lib.reservations import ReservationManager


def _make_mock_pool(mock_cursor=None):
    """Create a mock connection pool whose getconn() returns a mock conn."""
    if mock_cursor is None:
        mock_cursor = mock.Mock()
        mock_cursor.fetchone.return_value = (42,)
    mock_conn = mock.Mock()
    mock_conn.cursor.return_value.__enter__ = mock.Mock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)
    mock_pool = mock.Mock()
    mock_pool.getconn.return_value = mock_conn
    mock_pool.putconn = mock.Mock()
    return mock_pool, mock_conn


class TestBMC(unittest.TestCase):
    def test_power_cycle_success(self):
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout=b'Chassis Power Control: Cycle',
                stderr=b''
            )
            bmc = BMC('192.168.1.10', 'password')
            rc, stdout, stderr = bmc.power_cycle()

            self.assertEqual(rc, 0)
            self.assertIn('Cycle', stdout)
            args = mock_run.call_args[0][0]
            self.assertIn('ipmitool', args)
            self.assertIn('-I', args)
            self.assertIn('lanplus', args)
            self.assertIn('-E', args)
            self.assertNotIn('-P', args)
            self.assertIn('chassis', args)
            self.assertIn('power', args)
            self.assertIn('cycle', args)
            # Verify password passed via env
            call_kwargs = mock_run.call_args[1]
            self.assertEqual(call_kwargs['env']['IPMI_PASSWORD'], 'password')

    def test_power_cycle_failure(self):
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=1,
                stdout=b'',
                stderr=b'Unable to establish IPMI connection'
            )
            bmc = BMC('192.168.1.10', 'password')
            rc, stdout, stderr = bmc.power_cycle()

            self.assertEqual(rc, 1)

    def test_power_cycle_timeout(self):
        import subprocess
        with mock.patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd='ipmitool', timeout=30)
            bmc = BMC('192.168.1.10', 'password')
            rc, stdout, stderr = bmc.power_cycle()

            self.assertEqual(rc, 1)
            self.assertEqual(stderr, 'timeout')

    def test_sol_activate(self):
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0,
                stdout=b'SOL session activated',
                stderr=b''
            )
            bmc = BMC('192.168.1.10', 'password')
            rc, stdout, stderr = bmc.sol_activate()

            self.assertEqual(rc, 0)
            args = mock_run.call_args[0][0]
            self.assertIn('sol', args)
            self.assertIn('activate', args)

    def test_custom_user(self):
        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.Mock(
                returncode=0, stdout=b'', stderr=b''
            )
            bmc = BMC('192.168.1.10', 'password', bmc_user='ADMIN')
            bmc.power_status()

            args = mock_run.call_args[0][0]
            idx = args.index('-U')
            self.assertEqual(args[idx + 1], 'ADMIN')


class TestHealthChecker(unittest.TestCase):
    def _make_machines(self, states=None):
        machines = {
            1: {'name': 'test1', 'mgmt_ipaddr': '10.0.0.1',
                'state': MachineState.HEALTHY},
            2: {'name': 'test2', 'mgmt_ipaddr': '10.0.0.2',
                'state': MachineState.HEALTHY},
        }
        if states:
            for mid, state in states.items():
                machines[mid]['state'] = state
        return machines

    def _make_bmc_map(self):
        bmc_map = {}
        for mid in [1, 2]:
            bmc_map[mid] = mock.Mock()
            bmc_map[mid].power_cycle.return_value = (0, '', '')
        return bmc_map

    def test_healthy_stays_healthy(self):
        machines = self._make_machines()
        bmc_map = self._make_bmc_map()
        hc = HealthChecker(machines, bmc_map, interval=1)

        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            hc.check_machine(1, machines[1])

        self.assertEqual(machines[1]['state'], MachineState.HEALTHY)

    def test_healthy_to_miss_one(self):
        machines = self._make_machines()
        bmc_map = self._make_bmc_map()
        hc = HealthChecker(machines, bmc_map)

        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            hc.check_machine(1, machines[1])

        self.assertEqual(machines[1]['state'], MachineState.MISS_ONE)

    def test_miss_one_to_miss_two(self):
        machines = self._make_machines({1: MachineState.MISS_ONE})
        bmc_map = self._make_bmc_map()
        hc = HealthChecker(machines, bmc_map)

        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            hc.check_machine(1, machines[1])

        self.assertEqual(machines[1]['state'], MachineState.MISS_TWO)

    def test_miss_two_triggers_reboot(self):
        machines = self._make_machines({1: MachineState.MISS_TWO})
        bmc_map = self._make_bmc_map()
        hc = HealthChecker(machines, bmc_map)

        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            hc.check_machine(1, machines[1])

        # SSH unreachable -> power cycle
        self.assertEqual(machines[1]['state'], MachineState.POWER_CYCLE_ISSUED)
        bmc_map[1].power_cycle.assert_called_once()

    def test_power_cycle_issued_to_healthy(self):
        machines = self._make_machines({1: MachineState.POWER_CYCLE_ISSUED})
        bmc_map = self._make_bmc_map()
        hc = HealthChecker(machines, bmc_map)

        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.Mock(returncode=0)
            hc.check_machine(1, machines[1])

        self.assertEqual(machines[1]['state'], MachineState.HEALTHY)

    def test_power_cycle_issued_still_down(self):
        machines = self._make_machines({1: MachineState.POWER_CYCLE_ISSUED})
        bmc_map = self._make_bmc_map()
        hc = HealthChecker(machines, bmc_map)

        with mock.patch('subprocess.run') as mock_run:
            mock_run.return_value = mock.Mock(returncode=1)
            hc.check_machine(1, machines[1])

        # Restart miss counter
        self.assertEqual(machines[1]['state'], MachineState.MISS_ONE)

    def test_reserved_skipped(self):
        machines = self._make_machines({1: MachineState.RESERVED})
        bmc_map = self._make_bmc_map()
        hc = HealthChecker(machines, bmc_map)

        with mock.patch('subprocess.run') as mock_run:
            hc.check_machine(1, machines[1])
            mock_run.assert_not_called()

        self.assertEqual(machines[1]['state'], MachineState.RESERVED)

    def test_shared_lock(self):
        """HealthChecker and ReservationManager share the same lock."""
        machines = self._make_machines()
        bmc_map = self._make_bmc_map()
        shared_lock = threading.Lock()
        hc = HealthChecker(machines, bmc_map, lock=shared_lock)
        mock_pool, _ = _make_mock_pool()
        mgr = ReservationManager(mock_pool, 600, machines, bmc_map,
                                 lock=shared_lock)

        self.assertIs(hc.lock, mgr.lock)
        self.assertIs(hc.lock, shared_lock)


class TestSOLListener(unittest.TestCase):
    def test_receive_and_insert(self):
        mock_pool, mock_conn = _make_mock_pool()
        mock_cursor = mock.Mock()
        mock_conn.cursor.return_value.__enter__ = mock.Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)

        machines = {'192.168.1.10': 1}
        listener = SOLListener(6230, mock_pool, machines)

        listener._process_data(b'test log line\n', '192.168.1.10')

        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        self.assertIn('INSERT INTO sol', call_args[0][0])
        params = call_args[0][1]
        self.assertEqual(params[0], 1)  # machine_id
        self.assertEqual(params[2], 'test log line\n')  # line
        self.assertTrue(params[3])  # eol
        mock_pool.putconn.assert_called_once_with(mock_conn)

    def test_line_chunking(self):
        mock_pool, mock_conn = _make_mock_pool()
        mock_cursor = mock.Mock()
        mock_conn.cursor.return_value.__enter__ = mock.Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)

        machines = {'192.168.1.10': 1}
        listener = SOLListener(6230, mock_pool, machines)

        # Send a line longer than LINE_MAX (200)
        long_line = 'x' * 250 + '\n'
        listener._process_data(long_line.encode(), '192.168.1.10')

        # Should have 2 inserts: first 200 chars (eol=False), rest (eol=True)
        self.assertEqual(mock_cursor.execute.call_count, 2)
        first_call = mock_cursor.execute.call_args_list[0][0][1]
        second_call = mock_cursor.execute.call_args_list[1][0][1]
        self.assertFalse(first_call[3])  # first chunk not eol
        self.assertTrue(second_call[3])  # second chunk is eol

    def test_unknown_source_ip(self):
        mock_pool, mock_conn = _make_mock_pool()
        mock_cursor = mock.Mock()
        mock_conn.cursor.return_value.__enter__ = mock.Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)

        machines = {'192.168.1.10': 1}
        listener = SOLListener(6230, mock_pool, machines)

        listener._process_data(b'test data', '10.0.0.99')
        mock_cursor.execute.assert_not_called()


class TestReservationManager(unittest.TestCase):
    def _make_manager(self, machine_states=None):
        mock_pool, _ = _make_mock_pool()

        if machine_states is None:
            machine_states = {
                1: {'name': 'test1', 'state': MachineState.HEALTHY},
                2: {'name': 'test2', 'state': MachineState.HEALTHY},
            }

        bmc_map = {}
        for mid in machine_states:
            bmc_map[mid] = mock.Mock()
            bmc_map[mid].power_cycle.return_value = (0, '', '')

        mgr = ReservationManager(mock_pool, 600, machine_states, bmc_map)
        return mgr, machine_states, bmc_map

    def test_reserve_success(self):
        mgr, machines, _ = self._make_manager()
        rid, err = mgr.reserve('test-caller', [1, 2])

        self.assertEqual(rid, 42)
        self.assertIsNone(err)
        self.assertEqual(machines[1]['state'], MachineState.RESERVED)
        self.assertEqual(machines[2]['state'], MachineState.RESERVED)

    def test_reserve_unavailable(self):
        machines = {
            1: {'name': 'test1', 'state': MachineState.HEALTHY},
            2: {'name': 'test2', 'state': MachineState.MISS_ONE},
        }
        mgr, _, _ = self._make_manager(machines)
        rid, err = mgr.reserve('test-caller', [2])

        self.assertIsNone(rid)
        self.assertIn('MISS_ONE', err)

    def test_reserve_atomic(self):
        machines = {
            1: {'name': 'test1', 'state': MachineState.HEALTHY},
            2: {'name': 'test2', 'state': MachineState.RESERVED},
        }
        mgr, _, _ = self._make_manager(machines)
        rid, err = mgr.reserve('test-caller', [1, 2])

        self.assertIsNone(rid)
        # Machine 1 should still be HEALTHY (atomic: neither reserved)
        self.assertEqual(machines[1]['state'], MachineState.HEALTHY)

    def test_refresh_success(self):
        mgr, _, _ = self._make_manager()
        mgr.reserve('test-caller', [1])

        ok, err = mgr.refresh('test-caller', 42)
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_refresh_wrong_caller(self):
        mgr, _, _ = self._make_manager()
        mgr.reserve('test-caller', [1])

        ok, err = mgr.refresh('wrong-caller', 42)
        self.assertFalse(ok)
        self.assertIn('Wrong caller', err)

    def test_timeout(self):
        mgr, machines, bmc_map = self._make_manager()
        mgr.reserve('test-caller', [1])

        # Manipulate last_refresh to be old
        mgr.active[42]['last_refresh'] = (
            datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=700)
        )

        mgr.check_timeouts()

        self.assertEqual(machines[1]['state'], MachineState.POWER_CYCLE_ISSUED)
        self.assertNotIn(42, mgr.active)
        bmc_map[1].power_cycle.assert_called_once()

    def test_close(self):
        mgr, machines, bmc_map = self._make_manager()
        mgr.reserve('test-caller', [1, 2])

        ok, err = mgr.close('test-caller', 42)
        self.assertTrue(ok)
        self.assertEqual(machines[1]['state'], MachineState.POWER_CYCLE_ISSUED)
        self.assertEqual(machines[2]['state'], MachineState.POWER_CYCLE_ISSUED)
        bmc_map[1].power_cycle.assert_called_once()
        bmc_map[2].power_cycle.assert_called_once()

    def test_close_not_found(self):
        mgr, _, _ = self._make_manager()
        ok, err = mgr.close('test-caller', 999)
        self.assertFalse(ok)
        self.assertIn('not found', err)

    def test_reserve_unknown_machine(self):
        mgr, _, _ = self._make_manager()
        rid, err = mgr.reserve('test-caller', [99])
        self.assertIsNone(rid)
        self.assertIn('Unknown', err)

    def test_external_lock(self):
        """Verify external lock is used when provided."""
        mock_pool, _ = _make_mock_pool()
        machine_states = {
            1: {'name': 'test1', 'state': MachineState.HEALTHY},
        }
        bmc_map = {1: mock.Mock()}
        shared_lock = threading.Lock()
        mgr = ReservationManager(mock_pool, 600, machine_states, bmc_map,
                                 lock=shared_lock)
        self.assertIs(mgr.lock, shared_lock)


class TestGetSolLogs(unittest.TestCase):
    """Test the SOL log reconstruction logic from machine_control."""

    def test_line_reconstruction(self):
        # Import the function under test
        from machine_control import reconstruct_sol_lines

        rows = [
            (1, datetime.datetime(2024, 1, 1, 12, 0, 0), 'first half ', False),
            (2, datetime.datetime(2024, 1, 1, 12, 0, 0), 'second half', True),
            (3, datetime.datetime(2024, 1, 1, 12, 0, 1), 'full line', True),
        ]
        lines = reconstruct_sol_lines(rows)

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]['line'], 'first half second half')
        self.assertEqual(lines[1]['line'], 'full line')

    def test_pagination(self):
        from machine_control import reconstruct_sol_lines

        rows = [
            (5, datetime.datetime(2024, 1, 1), 'line 5', True),
            (6, datetime.datetime(2024, 1, 1), 'line 6', True),
        ]
        lines = reconstruct_sol_lines(rows)
        self.assertEqual(len(lines), 2)

    def test_sort_order(self):
        from machine_control import reconstruct_sol_lines

        # Just verify it handles rows in given order
        rows = [
            (10, datetime.datetime(2024, 1, 1), 'newer', True),
            (9, datetime.datetime(2024, 1, 1), 'older', True),
        ]
        lines = reconstruct_sol_lines(rows)
        self.assertEqual(lines[0]['line'], 'newer')
        self.assertEqual(lines[1]['line'], 'older')


class TestFlaskEndpoints(unittest.TestCase):
    def setUp(self):
        import machine_control as mc_mod
        self.mc_mod = mc_mod

        # Set up test state
        mc_mod.machines.clear()
        mc_mod.machines.update({
            1: {'name': 'test1', 'mgmt_ipaddr': '10.0.0.1',
                'state': MachineState.HEALTHY},
        })

        mc_mod.bmc_map.clear()
        mc_mod.bmc_map[1] = mock.Mock()
        mc_mod.bmc_map[1].power_cycle.return_value = (0, 'ok', '')

        mc_mod.auth_map.clear()

        mock_pool, mock_conn = _make_mock_pool()
        mock_cursor = mock.Mock()
        mock_cursor.fetchone.return_value = (1,)
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value.__enter__ = mock.Mock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = mock.Mock(return_value=False)
        mc_mod.db_pool = mock_pool

        mc_mod.res_mgr = ReservationManager(
            mock_pool, 600, mc_mod.machines, mc_mod.bmc_map
        )

        self.app = mc_mod.app.test_client()

    def test_get_machine_info(self):
        resp = self.app.get('/get_machine_info')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['name'], 'test1')
        self.assertEqual(data[0]['state'], 'HEALTHY')

    def test_get_nic_info(self):
        resp = self.app.get('/get_nic_info')
        self.assertEqual(resp.status_code, 200)

    def test_power_cycle(self):
        resp = self.app.post('/power_cycle',
                             json={'machine_id': 1, 'caller': 'test'})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['retcode'], 0)

    def test_power_cycle_missing_id(self):
        resp = self.app.post('/power_cycle', json={})
        self.assertEqual(resp.status_code, 400)

    def test_reserve_and_close(self):
        resp = self.app.post('/reserve',
                             json={'caller': 'test', 'machine_ids': [1]})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        rid = data['reservation_id']

        self.assertEqual(
            self.mc_mod.machines[1]['state'], MachineState.RESERVED
        )

        resp = self.app.post('/reservation_close',
                             json={'caller': 'test', 'reservation_id': rid})
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(
            self.mc_mod.machines[1]['state'], MachineState.POWER_CYCLE_ISSUED
        )

    def test_reservation_refresh(self):
        resp = self.app.post('/reserve',
                             json={'caller': 'test', 'machine_ids': [1]})
        rid = resp.get_json()['reservation_id']

        resp = self.app.post('/reservation_refresh',
                             json={'caller': 'test', 'reservation_id': rid})
        self.assertEqual(resp.status_code, 200)

    def test_reserve_unavailable(self):
        self.mc_mod.machines[1]['state'] = MachineState.MISS_ONE

        resp = self.app.post('/reserve',
                             json={'caller': 'test', 'machine_ids': [1]})
        self.assertEqual(resp.status_code, 409)

    def test_ip_auth_blocks(self):
        """Per-machine IP auth rejects unauthorized callers."""
        self.mc_mod.auth_map[1] = ['10.99.99.99']

        resp = self.app.post('/power_cycle',
                             json={'machine_id': 1, 'caller': 'test'})
        self.assertEqual(resp.status_code, 403)

    def test_ip_auth_no_restriction(self):
        """No allowed_ips means open access."""
        self.mc_mod.auth_map.clear()

        resp = self.app.post('/power_cycle',
                             json={'machine_id': 1, 'caller': 'test'})
        self.assertEqual(resp.status_code, 200)


class TestRecoverReservations(unittest.TestCase):
    def test_recovery(self):
        from machine_control import _recover_reservations

        mock_cursor = mock.Mock()
        mock_cursor.fetchall.return_value = [
            (10, 'test-caller', 1),
            (10, 'test-caller', 2),
            (11, 'other-caller', 1),
        ]
        mock_pool, _ = _make_mock_pool(mock_cursor)

        machine_states = {
            1: {'name': 'test1', 'state': MachineState.HEALTHY},
            2: {'name': 'test2', 'state': MachineState.HEALTHY},
        }
        bmc_map = {
            1: mock.Mock(),
            2: mock.Mock(),
        }
        mgr = ReservationManager(mock_pool, 600, machine_states, bmc_map)

        _recover_reservations(mock_pool, mgr)

        self.assertIn(10, mgr.active)
        self.assertIn(11, mgr.active)
        self.assertEqual(mgr.active[10]['caller'], 'test-caller')
        self.assertEqual(sorted(mgr.active[10]['machine_ids']), [1, 2])
        self.assertEqual(machine_states[1]['state'], MachineState.RESERVED)
        self.assertEqual(machine_states[2]['state'], MachineState.RESERVED)


if __name__ == '__main__':
    unittest.main()
