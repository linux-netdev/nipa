# SPDX-License-Identifier: GPL-2.0

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from contest.hw.local import _metric_rows, _prepare_route, _remove_peer_key, \
    _remove_routes, _resolve_tests, _select_installed_tests, _worker_problems


class TestLocalRoutes(unittest.TestCase):
    def test_installs_and_removes_host_route(self):
        current = mock.Mock(stdout=json.dumps([{
            'dev': 'eth0',
            'prefsrc': '2001:db8::10',
        }]))
        desired = mock.Mock(stdout=json.dumps([{
            'dev': 'eth1',
            'gateway': 'fe80::1',
            'prefsrc': '2001:db8:1::1',
        }]))
        installed = mock.Mock(returncode=0)
        removed = mock.Mock(returncode=0)

        with mock.patch('contest.hw.local._ssh',
                        side_effect=[current, desired, installed, removed]) as ssh:
            route = _prepare_route(
                'dut', 6, '2001:db8:1::1', '2001:db8:2::1', 'eth1')
            _remove_routes([route])

        self.assertEqual(
            ssh.call_args_list[2].args,
            ('dut', 'ip -6 route add 2001:db8:2::1/128 via fe80::1 '
                    'dev eth1 src 2001:db8:1::1 proto static'))
        self.assertEqual(
            ssh.call_args_list[3].args,
            ('dut', 'ip -6 route del 2001:db8:2::1/128 via fe80::1 '
                    'dev eth1 src 2001:db8:1::1 proto static'))

    def test_preserves_matching_route(self):
        current = mock.Mock(stdout=json.dumps([{
            'dev': 'eth1',
            'prefsrc': '2001:db8:1::1',
        }]))
        with mock.patch('contest.hw.local._ssh', return_value=current) as ssh:
            route = _prepare_route(
                'dut', 6, '2001:db8:1::1', '2001:db8:2::1', 'eth1')

        self.assertIsNone(route)
        ssh.assert_called_once()


class TestLocalPeerKey(unittest.TestCase):
    KEYS = ('ssh-ed25519 AAAA admin\n'
            'ssh-ed25519 BBBB nipa-local-1234\n')

    @staticmethod
    def _ssh_here(home):
        def ssh(_host, command, **kwargs):
            return subprocess.run(['sh', '-c', command],
                                  env={**os.environ, 'HOME': str(home)},
                                  check=kwargs.get('check', True))
        return ssh

    def _authorized_keys(self, temporary):
        path = Path(temporary) / '.ssh' / 'authorized_keys'
        path.parent.mkdir()
        path.write_text(self.KEYS, encoding='utf-8')
        return path

    def test_removes_only_the_temporary_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._authorized_keys(temporary)
            with mock.patch('contest.hw.local._ssh',
                            side_effect=self._ssh_here(temporary)):
                _remove_peer_key('peer', 'nipa-local-1234')

            self.assertEqual(path.read_text(encoding='utf-8'),
                             'ssh-ed25519 AAAA admin\n')
            self.assertEqual([entry.name for entry in path.parent.iterdir()],
                             ['authorized_keys'])

    @unittest.skipIf(os.geteuid() == 0, 'root ignores file permissions')
    def test_keeps_keys_when_no_copy_can_be_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = self._authorized_keys(temporary)
            # authorized_keys stays writable, its directory does not
            path.parent.chmod(0o500)
            try:
                with mock.patch('contest.hw.local._ssh',
                                side_effect=self._ssh_here(temporary)), \
                        self.assertRaises(subprocess.CalledProcessError):
                    _remove_peer_key('peer', 'nipa-local-1234')
            finally:
                path.parent.chmod(0o700)

            self.assertEqual(path.read_text(encoding='utf-8'), self.KEYS)


class TestLocalWorkerStatus(unittest.TestCase):
    def test_sentinels_report_an_incomplete_run(self):
        stdout = ('[1/2] Running drivers/net/perf:devmem.py\n'
                  'NIPA DETECTED SYSTEM CRASH\n'
                  'NIPA DETECTED TEST TIMEOUT\n')

        self.assertEqual(_worker_problems(stdout), [
            'NIPA DETECTED SYSTEM CRASH', 'NIPA DETECTED TEST TIMEOUT',
        ])
        self.assertEqual(_worker_problems('[1/2] Running x\n'), [])


class TestLocalTestPaths(unittest.TestCase):
    def test_resolves_tests_in_linux_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            root = tree / 'tools/testing/selftests'
            (root / 'run_kselftest.sh').parent.mkdir(parents=True)
            (root / 'run_kselftest.sh').write_text('', encoding='utf-8')
            target = root / 'drivers/net/perf'
            target.mkdir(parents=True)
            (target / 'iperf.py').write_text('', encoding='utf-8')
            (target / 'devmem.py').write_text('', encoding='utf-8')
            selected = [
                'drivers/net/perf:iperf.py',
                'drivers/net/perf:devmem.py',
            ]

            test_root, tests = _resolve_tests(tree, selected)

            self.assertEqual(test_root, root)
            self.assertEqual(tests, selected)

    def test_resolves_and_expands_collection(self):
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            root = tree / 'tools/testing/selftests'
            (root / 'run_kselftest.sh').parent.mkdir(parents=True)
            (root / 'run_kselftest.sh').write_text('', encoding='utf-8')
            target = root / 'drivers/net/perf'
            target.mkdir(parents=True)

            test_root, selectors = _resolve_tests(
                tree, ['drivers/net/perf'])

            installed = tree / 'installed'
            installed.mkdir()
            (installed / 'kselftest-list.txt').write_text(
                'drivers/net/perf:devmem.py\n'
                'drivers/net/perf:iperf.py\n'
                'drivers/net/hw:ethtool.sh\n', encoding='utf-8')
            tests = _select_installed_tests(installed, selectors)

            self.assertEqual(test_root, root)
            self.assertEqual(tests, [
                'drivers/net/perf:devmem.py',
                'drivers/net/perf:iperf.py',
            ])

    def test_rejects_invalid_test_identifier(self):
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            root = tree / 'tools/testing/selftests'
            root.mkdir(parents=True)
            (root / 'run_kselftest.sh').write_text('', encoding='utf-8')

            with self.assertRaisesRegex(RuntimeError,
                                        r'target\[:program\]'):
                _resolve_tests(tree, ['drivers/net/perf:'])


class TestLocalMetricStore(unittest.TestCase):
    def test_reads_result_json_without_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'result.json'
            path.write_text(json.dumps({
                'branch': 'local-test',
                'branch_date': '2026-09-16--12-00',
                'remote': 'local',
                'executor': 'dut',
                'start': '2026-09-16T12:00:00+00:00',
                'results': [{
                    'group': 'selftests-net',
                    'test': 'example-py',
                    'result': 'pass',
                    'link': '.',
                    'results': [{
                        'test': 'case-one',
                        'result': 'pass',
                        'metrics': [{
                            'name': 'throughput',
                            'type': 'scalar',
                            'value': 10.0,
                            'host': 'local',
                            'summarize': 'p50',
                        }],
                    }],
                }],
            }), encoding='utf-8')

            overview, details, roots = _metric_rows([path])
            self.assertEqual(len(overview), 1)
            self.assertEqual(overview[0]['executor'], 'dut')
            self.assertEqual(
                overview[0]['branch'],
                'local-test-2026-09-16--12-00')
            self.assertEqual(
                overview[0]['short_json'][0]['summary']['p50'], 10.0)
            self.assertEqual(details[0]['full_json'][0]['name'], 'throughput')
            self.assertEqual(roots['0'], path.parent)


if __name__ == '__main__':
    unittest.main()
