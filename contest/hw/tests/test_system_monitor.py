# SPDX-License-Identifier: GPL-2.0

import json
import os
import tempfile
import unittest
from unittest import mock

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


if __name__ == '__main__':
    unittest.main()
