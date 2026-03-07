# SPDX-License-Identifier: GPL-2.0

import json
import unittest
from io import StringIO
from unittest import mock

import sys
import os

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mc_cli import main  # noqa: E402


MOCK_MACHINES = [
    {'id': 1, 'name': 'box1', 'mgmt_ipaddr': '10.0.0.1', 'state': 'HEALTHY'},
    {'id': 2, 'name': 'box2', 'mgmt_ipaddr': '10.0.0.2', 'state': 'RESERVED'},
]

MOCK_NICS = [
    {'id': 1, 'machine_id': 1, 'vendor': 'acme', 'model': 'nic100',
     'ifname': 'eth0', 'peer_id': 2, 'ip4addr': '10.1.0.1',
     'ip6addr': 'fd00::1'},
    {'id': 2, 'machine_id': 2, 'vendor': 'acme', 'model': 'nic100',
     'ifname': 'eth0', 'peer_id': 1, 'ip4addr': '10.1.0.2',
     'ip6addr': 'fd00::2'},
]


def _run(argv):
    """Run main() with argv, capture stdout and stderr, return (rc, out, err)."""
    out = StringIO()
    err = StringIO()
    with mock.patch('sys.stdout', out), mock.patch('sys.stderr', err):
        rc = main(argv)
    return rc, out.getvalue(), err.getvalue()


class TestMachines(unittest.TestCase):
    @mock.patch('mc_cli.MCClient')
    def test_list(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_machine_info.return_value = MOCK_MACHINES

        rc, out, _ = _run(['--url', 'http://x', 'machines'])

        self.assertEqual(rc, 0)
        self.assertIn('box1', out)
        self.assertIn('HEALTHY', out)
        self.assertIn('box2', out)

    @mock.patch('mc_cli.MCClient')
    def test_list_json(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_machine_info.return_value = MOCK_MACHINES

        rc, out, _ = _run(['--url', 'http://x', '--json', 'machines'])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]['name'], 'box1')

    @mock.patch('mc_cli.MCClient')
    def test_empty(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_machine_info.return_value = []

        rc, out, _ = _run(['--url', 'http://x', 'machines'])

        self.assertEqual(rc, 0)
        self.assertIn('No machines found', out)


class TestNics(unittest.TestCase):
    @mock.patch('mc_cli.MCClient')
    def test_list(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_nic_info.return_value = MOCK_NICS

        rc, out, _ = _run(['--url', 'http://x', 'nics'])

        self.assertEqual(rc, 0)
        self.assertIn('eth0', out)
        self.assertIn('acme', out)

    @mock.patch('mc_cli.MCClient')
    def test_filter(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_nic_info.return_value = [MOCK_NICS[0]]

        rc, out, _ = _run(['--url', 'http://x', 'nics', '--nic-id', '1'])

        self.assertEqual(rc, 0)
        mc.get_nic_info.assert_called_once_with(nic_id=1)


class TestResolve(unittest.TestCase):
    @mock.patch('mc_cli.MCClient')
    def test_two_machines(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_nic_info.return_value = MOCK_NICS

        rc, out, _ = _run(['--url', 'http://x', 'resolve', '--nic-id', '1'])

        self.assertEqual(rc, 0)
        self.assertIn('1, 2', out)

    @mock.patch('mc_cli.MCClient')
    def test_json(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_nic_info.return_value = MOCK_NICS

        rc, out, _ = _run(['--url', 'http://x', '--json', 'resolve',
                           '--nic-id', '1'])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data['machine_ids'], [1, 2])


class TestSol(unittest.TestCase):
    @mock.patch('mc_cli.MCClient')
    def test_logs(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_sol_logs.return_value = {
            'machine_id': 1, 'last_id': 42,
            'lines': [{'ts': '2024-01-01 00:00', 'line': 'boot msg'}],
        }

        rc, out, err = _run(['--url', 'http://x', 'sol',
                             '--machine-id', '1'])

        self.assertEqual(rc, 0)
        self.assertIn('boot msg', out)
        self.assertIn('last_id=42', err)

    @mock.patch('mc_cli.MCClient')
    def test_start_id(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_sol_logs.return_value = {
            'machine_id': 1, 'last_id': 100, 'lines': [],
        }

        _run(['--url', 'http://x', 'sol', '--machine-id', '1',
              '--start-id', '50', '--limit', '200'])

        mc.get_sol_logs.assert_called_once_with(1, start_id=50, limit=200)

    @mock.patch('mc_cli.MCClient')
    def test_json_no_stderr(self, mock_cls):
        """In --json mode, last_id is in the JSON — don't also print to stderr."""
        mc = mock_cls.return_value
        mc.get_sol_logs.return_value = {
            'machine_id': 1, 'last_id': 42,
            'lines': [{'ts': '2024-01-01', 'line': 'test'}],
        }

        rc, out, err = _run(['--url', 'http://x', '--json', 'sol',
                             '--machine-id', '1'])

        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data['last_id'], 42)
        self.assertEqual(err, '')


class TestReserve(unittest.TestCase):
    @mock.patch('mc_cli.MCClient')
    def test_success(self, mock_cls):
        mc = mock_cls.return_value
        mc.reserve.return_value = {'reservation_id': 42, 'timeout': 600}

        rc, out, _ = _run(['--url', 'http://x', 'reserve',
                           '--machine-ids', '1,2'])

        self.assertEqual(rc, 0)
        self.assertIn('42', out)
        mc.reserve.assert_called_once_with([1, 2], timeout=None)

    @mock.patch('mc_cli.MCClient')
    def test_failure(self, mock_cls):
        mc = mock_cls.return_value
        mc.reserve.return_value = {'error': 'Machine reserved'}

        rc, _, err = _run(['--url', 'http://x', 'reserve',
                           '--machine-ids', '1'])

        self.assertEqual(rc, 1)
        self.assertIn('Machine reserved', err)

    @mock.patch('mc_cli.MCClient')
    def test_with_timeout(self, mock_cls):
        mc = mock_cls.return_value
        mc.reserve.return_value = {'reservation_id': 7, 'timeout': 120}

        _run(['--url', 'http://x', 'reserve', '--machine-ids', '1',
              '--timeout', '120'])

        mc.reserve.assert_called_once_with([1], timeout=120)

    @mock.patch('mc_cli.MCClient')
    def test_whitespace_in_machine_ids(self, mock_cls):
        mc = mock_cls.return_value
        mc.reserve.return_value = {'reservation_id': 8, 'timeout': 600}

        _run(['--url', 'http://x', 'reserve', '--machine-ids', '1, 2, '])

        mc.reserve.assert_called_once_with([1, 2], timeout=None)


class TestRefresh(unittest.TestCase):
    @mock.patch('mc_cli.MCClient')
    def test_success(self, mock_cls):
        mc = mock_cls.return_value
        mc.reservation_refresh.return_value = {'ok': True}

        rc, out, _ = _run(['--url', 'http://x', 'refresh',
                           '--reservation-id', '42'])

        self.assertEqual(rc, 0)
        self.assertIn('Refreshed', out)

    @mock.patch('mc_cli.MCClient')
    def test_failure(self, mock_cls):
        mc = mock_cls.return_value
        mc.reservation_refresh.return_value = {'error': 'not found'}

        rc, _, err = _run(['--url', 'http://x', 'refresh',
                           '--reservation-id', '99'])

        self.assertEqual(rc, 1)
        self.assertIn('not found', err)


class TestClose(unittest.TestCase):
    @mock.patch('mc_cli.MCClient')
    def test_success(self, mock_cls):
        mc = mock_cls.return_value
        mc.reservation_close.return_value = {'ok': True}

        rc, out, _ = _run(['--url', 'http://x', 'close',
                           '--reservation-id', '42'])

        self.assertEqual(rc, 0)
        self.assertIn('Closed', out)


class TestPowerCycle(unittest.TestCase):
    @mock.patch('mc_cli.MCClient')
    def test_success(self, mock_cls):
        mc = mock_cls.return_value
        mc.power_cycle.return_value = {
            'machine_id': 1, 'retcode': 0,
            'stdout': 'Chassis Power Control: Cycle',
            'stderr': '',
        }

        rc, out, _ = _run(['--url', 'http://x', 'power-cycle',
                           '--machine-id', '1'])

        self.assertEqual(rc, 0)
        self.assertIn('retcode=0', out)
        self.assertIn('Chassis Power Control', out)

    @mock.patch('mc_cli.MCClient')
    def test_failure(self, mock_cls):
        mc = mock_cls.return_value
        mc.power_cycle.return_value = {
            'machine_id': 1, 'retcode': 1,
            'stdout': '', 'stderr': 'connection refused',
        }

        rc, out, _ = _run(['--url', 'http://x', 'power-cycle',
                           '--machine-id', '1'])

        self.assertEqual(rc, 1)
        self.assertIn('connection refused', out)


class TestGlobalArgs(unittest.TestCase):
    def test_no_command(self):
        rc, _, _ = _run(['--url', 'http://x'])
        self.assertEqual(rc, 2)

    def test_no_url(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MC_URL', None)
            with self.assertRaises(SystemExit):
                _run(['machines'])

    @mock.patch('mc_cli.MCClient')
    def test_caller(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_machine_info.return_value = []

        _run(['--url', 'http://x', '--caller', 'test-user', 'machines'])

        mock_cls.assert_called_once_with('http://x', caller='test-user')

    @mock.patch('mc_cli.MCClient')
    def test_url_from_env(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_machine_info.return_value = []

        with mock.patch.dict(os.environ, {'MC_URL': 'http://env-url'}):
            _run(['machines'])

        mock_cls.assert_called_once_with('http://env-url', caller='cli')

    @mock.patch('mc_cli.MCClient')
    def test_connection_error(self, mock_cls):
        mc = mock_cls.return_value
        mc.get_machine_info.side_effect = requests.ConnectionError(
            'Connection refused')

        rc, _, err = _run(['--url', 'http://x', 'machines'])

        self.assertEqual(rc, 1)
        self.assertIn('Connection refused', err)

    @mock.patch('mc_cli.MCClient')
    def test_http_error(self, mock_cls):
        mc = mock_cls.return_value
        mc.power_cycle.side_effect = requests.HTTPError('404 Not Found')

        rc, _, err = _run(['--url', 'http://x', 'power-cycle',
                           '--machine-id', '99'])

        self.assertEqual(rc, 1)
        self.assertIn('404 Not Found', err)


if __name__ == '__main__':
    unittest.main()
