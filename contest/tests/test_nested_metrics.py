# SPDX-License-Identifier: GPL-2.0

import json
import os
import unittest

from contest.remote.lib.names import namify
from contest.remote.lib.results import parse_nested_tests


class TestNestedMetrics(unittest.TestCase):
    def test_real_devmem_output(self):
        """All metrics from the current devmem run belong to the kperf case."""
        fixture = os.path.join(os.path.dirname(__file__), 'fixtures',
                               'devmem-metrics.tap')
        with open(fixture, encoding='utf-8') as fp:
            output = fp.read()
        self.assertEqual(output.count('# # ktap-metric-json: '), 1)
        for line in output.splitlines():
            marker = '# # ktap-metric-json: '
            if line.startswith(marker):
                observation = json.loads(line[len(marker):])
                self.assertEqual(set(observation), {'name', 'value', 'goal'})

        nested = parse_nested_tests(output, namify)
        self.assertEqual(len(nested), 1)
        self.assertEqual(nested[0]['test'], 'devmem-kperf')
        self.assertEqual(nested[0]['result'], 'pass')
        metrics = nested[0]['metrics']
        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]['name'], 'throughput')
        self.assertAlmostEqual(metrics[0]['value'], 389.127)
        self.assertEqual(metrics[0]['goal'], 'higher')


    def test_retry_metrics_are_separate(self):
        first = ('# TAP version 13\n'
                 '# # ktap-metric-json: {"name":"throughput",'
                 '"goal":"higher","value":300,"host":"local"}\n'
                 '# not ok 1 devmem.kperf\n')
        retry = ('# TAP version 13\n'
                 '# # ktap-metric-json: {"name":"throughput",'
                 '"goal":"higher","value":320,"host":"local"}\n'
                 '# ok 1 devmem.kperf\n')

        nested = parse_nested_tests(first, namify)
        parse_nested_tests(retry, namify, prev_results=nested)

        self.assertEqual(nested[0]['result'], 'fail')
        self.assertEqual(nested[0]['retry'], 'pass')
        self.assertEqual(nested[0]['metrics'][0]['value'], 300.0)
        self.assertEqual(nested[0]['retry_metrics'][0]['value'], 320.0)

    def test_metric_fields_are_kept(self):
        output = (
            '# TAP version 13\n'
            '# # ktap-metric-json: {"name":"ring.rx_alloc_fail",'
            '"goal":"zero","host":"remote","queue":3,"value":0}\n'
            '# ok 1 devmem.kperf\n')

        nested = parse_nested_tests(output, namify)

        self.assertEqual(nested[0]['metrics'][0], {
            'name': 'ring.rx_alloc_fail',
            'goal': 'zero',
            'host': 'remote',
            'queue': 3,
            'value': 0,
        })

    def test_malformed_metrics_are_ignored(self):
        output = ('# TAP version 13\n'
                  '# # ktap-metric-json: not-json\n'
                  '# # ktap-metric-json: []\n'
                  '# # ktap-metric-json: {"name":1}\n'
                  '# # ktap-metric-json: {"name":"x","value":NaN}\n'
                  '# # ktap-metric-json: {"name":"x","value":1e400}\n'
                  '# # ktap-metric-json: {"name":"x","value":' +
                  '[' * 100000 + ']' * 100000 + '}\n'
                  '# ok 1 devmem.kperf\n')

        nested = parse_nested_tests(output, namify)

        self.assertNotIn('metrics', nested[0])


if __name__ == '__main__':
    unittest.main()
