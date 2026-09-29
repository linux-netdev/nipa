# SPDX-License-Identifier: GPL-2.0

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from contest.hw.lib.result_parser import parse_results
from contest.hw.lib.system_monitor import SystemMonitor, metrics_for_window


def _cpu(user, idle):
    return {
        0: {
            'user': float(user), 'nice': 0.0, 'sys': 0.0,
            'idle': float(idle), 'iowait': 0.0, 'irq': 0.0,
            'sirq': 0.0, 'steal': 0.0,
        },
    }


def _sample(timestamp, user, idle, dropped):
    return {
        'time': timestamp,
        'hosts': {
            'local': {
                'cpu': _cpu(user, idle),
                'nic': {
                    'rx_dropped': float(dropped),
                    'rx_missed_errors': 0.0,
                    'tx_dropped': 0.0,
                },
            },
        },
    }


class TestMetricWindows(unittest.TestCase):
    def test_interpolates_window_boundaries(self):
        metrics = metrics_for_window([
            _sample(0.0, 0, 0, 0),
            _sample(1.0, 50, 50, 10),
            _sample(2.0, 100, 100, 20),
        ], 0.5, 1.5)

        user = [metric for metric in metrics
                if metric['name'] == 'cpu.time.usr']
        self.assertEqual([metric['time'] for metric in user], [0.0, 0.5, 1.0])
        self.assertEqual([metric['value'] for metric in user],
                         [[0.0], [0.25], [0.5]])

        utilization = [metric for metric in metrics
                       if metric['name'] == 'cpu.utilization']
        self.assertEqual([metric['value'] for metric in utilization],
                         [[50.0], [50.0]])

        dropped = [metric for metric in metrics
                   if metric['name'] == 'nic.rx.dropped']
        self.assertEqual([metric['value'] for metric in dropped],
                         [0.0, 5.0, 10.0])

    def test_windows_the_samples_miss_have_no_metrics(self):
        samples = [_sample(1.0, 0, 0, 0), _sample(2.0, 50, 50, 10)]

        # Sampling stopped before the end, or started after the beginning
        self.assertEqual(metrics_for_window(samples, 1.5, 3.0), [])
        self.assertEqual(metrics_for_window(samples, 0.5, 1.5), [])
        self.assertNotEqual(metrics_for_window(samples, 1.0, 2.0), [])


class TestSystemMetricWriter(unittest.TestCase):
    def test_failed_sampler_writes_no_metrics(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = os.path.join(temporary, 'system-metrics.json')
            monitor = mock.Mock(error='remote system sampler stopped')
            monitor.stop.return_value = [
                _sample(0.0, 0, 0, 0), _sample(2.0, 100, 100, 20),
            ]

            data = SystemMonitor.write_case_metrics(
                monitor, path, [{'name': 'case', 'start': 0.5, 'end': 1.5}])

            self.assertEqual(data['cases'], [{
                'name': 'case', 'start': 0.5, 'end': 1.5, 'metrics': [],
            }])
            self.assertEqual(data['error'], 'remote system sampler stopped')

    def test_writes_sidecar_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = os.path.join(temporary, 'system-metrics.json')
            monitor = mock.Mock(error=None)
            monitor.stop.return_value = []

            with mock.patch('contest.hw.lib.system_monitor.os.replace',
                            wraps=os.replace) as replace:
                data = SystemMonitor.write_case_metrics(monitor, path, [])

            replace.assert_called_once_with(path + '.tmp', path)
            self.assertFalse(os.path.exists(path + '.tmp'))
            with open(path, encoding='utf-8') as fp:
                self.assertEqual(json.load(fp), data)


class TestSystemMetricMerge(unittest.TestCase):
    def test_merges_sidecar_by_case_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'test-outputs' / '0-example-py'
            output.mkdir(parents=True)
            (output / 'info').write_text(json.dumps({
                'retcode': 0,
                'target': 'drivers/net/hw',
                'prog': 'example.py',
            }), encoding='utf-8')
            (output / 'stdout').write_text(
                'TAP version 13\n'
                '1..1\n'
                '# TAP version 13\n'
                '# 1..1\n'
                '# ok 1 first case\n'
                'ok 1 selftests: drivers/net/hw: example.py\n',
                encoding='utf-8')
            metric = {
                'name': 'cpu.utilization',
                'type': 'per-cpu',
                'time': 1.0,
                'value': [50.0],
                'host': 'local',
                'summarize': 'p90',
                'aggregation': 'busy-core-equivalents',
            }
            (output / 'system-metrics.json').write_text(json.dumps({
                'version': 1,
                'cases': [{'name': 'first case', 'metrics': [metric]}],
            }), encoding='utf-8')

            results = parse_results(str(root), '.')
            metric['origin'] = 'system-monitor'
            self.assertEqual(results[0]['results'][0]['metrics'], [metric])

    def test_ignores_invalid_sidecar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'test-outputs' / '0-example-py'
            output.mkdir(parents=True)
            (output / 'info').write_text(json.dumps({
                'retcode': 0,
                'target': 'drivers/net/hw',
                'prog': 'example.py',
            }), encoding='utf-8')
            (output / 'stdout').write_text(
                '# TAP version 13\n# 1..1\n# ok 1 first case\n',
                encoding='utf-8')
            sidecar = output / 'system-metrics.json'

            invalid_data = (
                '{',
                json.dumps({
                    'version': 1,
                    'cases': [{'name': 'first case'}],
                }),
            )
            for data in invalid_data:
                with self.subTest(data=data):
                    sidecar.write_text(data, encoding='utf-8')
                    with mock.patch('builtins.print') as warning:
                        results = parse_results(str(root), '.')
                    warning.assert_called_once()
                    self.assertNotIn(
                        'metrics', results[0]['results'][0])


if __name__ == '__main__':
    unittest.main()
