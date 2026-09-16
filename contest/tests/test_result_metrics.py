# SPDX-License-Identifier: GPL-2.0

import unittest

from contest.backend.result_metrics import extract_result_metrics


class TestResultMetrics(unittest.TestCase):
    def test_extracts_initial_and_retry_rows(self):
        data = {
            'results': [{
                'group': 'selftests-drivers-net-hw',
                'test': 'devmem-py',
                'link': ('https://netdev.bots.linux.dev/results/123456/'
                         'test-outputs/0-devmem-py/'),
                'results': [{
                    'test': 'devmem-kperf',
                    'result': 'fail',
                    'retry': 'pass',
                    'metrics': [{
                        'name': 'throughput', 'goal': 'higher',
                        'value': 300.0, 'host': 'local',
                    }],
                    'retry_metrics': [{
                        'name': 'throughput', 'goal': 'higher',
                        'value': 320.0, 'host': 'local',
                    }],
                }],
            }],
        }

        rows = extract_result_metrics(data)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], {
            'group': 'selftests-drivers-net-hw',
            'test': 'devmem-py',
            'subtest': 'devmem-kperf',
            'link': ('https://netdev.bots.linux.dev/results/123456/'
                     'test-outputs/0-devmem-py/'),
            'short_json': [{
                'name': 'throughput',
                'goal': 'higher',
                'host': 'local',
                'display_label': 'Gbps',
                'summarize': 'last',
                'samples': 1,
                'summary': {'last': 300.0},
                'regression_value': 300.0,
                'value_count': 1,
            }],
            'full_json': [{
                'name': 'throughput',
                'goal': 'higher',
                'host': 'local',
                'value': 300.0,
            }],
            'attempt': 0,
            'result': 'fail',
        })
        self.assertEqual(rows[1]['short_json'][0]['summary']['last'], 320.0)
        self.assertEqual(rows[1]['full_json'][0]['value'], 320.0)
        self.assertEqual(rows[1]['attempt'], 1)
        self.assertEqual(rows[1]['result'], 'pass')

    def test_short_json_summarizes_every_series(self):
        metrics = [
            {'name': 'throughput', 'type': 'scalar', 'host': 'local',
             'time': 2.0, 'value': 320.0, 'summarize': 'p50'},
            {'name': 'throughput', 'type': 'scalar', 'host': 'local',
             'time': 1.0, 'value': 300.0, 'summarize': 'p50'},
            {'name': 'cpu.utilization', 'type': 'per-cpu', 'host': 'local',
             'time': 1.0, 'value': [10.0, None, 90.0],
             'aggregation': 'busy-core-equivalents', 'summarize': 'p90'},
            {'name': 'cpu.utilization', 'type': 'per-cpu', 'host': 'local',
             'time': 2.0, 'value': [100.0, 20.0, 30.0],
             'aggregation': 'busy-core-equivalents', 'summarize': 'p90'},
        ]
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass', 'metrics': metrics,
                }],
            }],
        }

        row = extract_result_metrics(data)[0]

        self.assertEqual(row['full_json'], metrics)
        self.assertEqual(row['short_json'], [
            {
                'name': 'throughput', 'type': 'scalar', 'host': 'local',
                'samples': 2, 'value_count': 2, 'summarize': 'p50',
                'display_label': 'Gbps', 'summary': {'p50': 300.0},
            },
            {
                'name': 'cpu.utilization', 'type': 'per-cpu',
                'host': 'local', 'samples': 2, 'value_count': 2,
                'aggregation': 'busy-core-equivalents',
                'summarize': 'p90', 'summary': {'p90': 1.5},
                'cpus': 3,
            },
        ])

    def test_metric_name_does_not_imply_an_aggregation_policy(self):
        metrics = [
            {'name': 'cpu.time.unconfigured', 'type': 'per-cpu',
             'host': 'local', 'time': 1.0, 'value': [0.0, 1.0],
             'summarize': 'p50'},
            {'name': 'cpu.time.unconfigured', 'type': 'per-cpu',
             'host': 'local', 'time': 2.0, 'value': [1.0, 3.0],
             'summarize': 'p50'},
        ]
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass', 'metrics': metrics,
                }],
            }],
        }

        summary = extract_result_metrics(data)[0]['short_json'][0]

        self.assertEqual(summary, {
            'name': 'cpu.time.unconfigured', 'type': 'per-cpu',
            'host': 'local', 'samples': 2, 'cpus': 2,
            'summarize': 'p50', 'summary': {'p50': 1.0},
            'value_count': 4,
        })

    def test_producer_metadata_drives_rate_and_cpu_aggregation(self):
        metrics = [
            {'name': 'custom.cpu.counter', 'type': 'per-cpu',
             'host': 'local', 'time': 1.0, 'value': [0.0, 1.0, None],
             'transform': 'rate',
             'aggregation': 'busy-core-equivalents', 'summarize': 'p75',
             'display_range': {'min': 0, 'max': 100},
             'display_scale': 100,
             'display_label': 'Percent of one CPU'},
            {'name': 'custom.cpu.counter', 'type': 'per-cpu',
             'host': 'local', 'time': 3.0, 'value': [1.0, 2.5, 3.0],
             'transform': 'rate',
             'aggregation': 'busy-core-equivalents', 'summarize': 'p75',
             'display_range': {'min': 0, 'max': 100},
             'display_scale': 100,
             'display_label': 'Percent of one CPU'},
        ]
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass', 'metrics': metrics,
                }],
            }],
        }

        summary = extract_result_metrics(data)[0]['short_json'][0]

        self.assertEqual(summary, {
            'name': 'custom.cpu.counter', 'type': 'per-cpu', 'host': 'local',
            'samples': 2, 'transform': 'rate',
            'aggregation': 'busy-core-equivalents', 'summarize': 'p75',
            'display_range': {'min': 0, 'max': 100},
            'display_scale': 100,
            'display_label': 'Percent of one CPU',
            'cpus': 3, 'summary': {'p75': 1.25},
            'value_count': 1,
        })

    def test_percentile_metadata_summarizes_one_sample(self):
        metrics = [{
            'name': 'custom.throughput', 'type': 'scalar', 'host': 'local',
            'time': 1.0, 'value': 42, 'summarize': 'p75',
        }]
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass', 'metrics': metrics,
                }],
            }],
        }

        summary = extract_result_metrics(data)[0]['short_json'][0]

        self.assertEqual(summary, {
            'name': 'custom.throughput', 'type': 'scalar', 'host': 'local',
            'samples': 1, 'summarize': 'p75',
            'summary': {'p75': 42}, 'value_count': 1,
        })

    def test_selftest_metrics_summarize_their_value(self):
        metrics = [
            {'name': 'rr.latency.p99', 'goal': 'lower', 'host': 'remote',
             'unit': 'us', 'value': 25},
            {'name': 'ring.rx_alloc_fail', 'goal': 'zero', 'host': 'remote',
             'value': 0},
        ]

        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass', 'metrics': metrics,
                }],
            }],
        }

        self.assertEqual(extract_result_metrics(data)[0]['short_json'], [
            {
                'name': 'rr.latency.p99', 'goal': 'lower', 'host': 'remote',
                'unit': 'us', 'display_label': 'us', 'samples': 1,
                'summarize': 'last',
                'summary': {'last': 25}, 'regression_value': 25,
                'value_count': 1,
            },
            {
                'name': 'ring.rx_alloc_fail', 'goal': 'zero',
                'host': 'remote', 'samples': 1, 'summarize': 'last',
                'summary': {'last': 0}, 'regression_value': 0,
                'value_count': 1,
            },
        ])

    def test_reported_unit_wins_over_known_one(self):
        metrics = [{'name': 'throughput', 'goal': 'higher', 'unit': 'Mbps',
                    'value': 940}]
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass', 'metrics': metrics,
                }],
            }],
        }

        summary = extract_result_metrics(data)[0]['short_json'][0]

        self.assertEqual(summary['unit'], 'Mbps')
        self.assertEqual(summary['display_label'], 'Mbps')

    def test_scalar_counter_metadata_drives_rate_percentile(self):
        metrics = [
            {'name': 'custom.counter', 'type': 'scalar', 'host': 'local',
             'time': 0.0, 'value': 0, 'transform': 'rate',
             'summarize': 'p50'},
            {'name': 'custom.counter', 'type': 'scalar', 'host': 'local',
             'time': 1.0, 'value': 2, 'transform': 'rate',
             'summarize': 'p50'},
            {'name': 'custom.counter', 'type': 'scalar', 'host': 'local',
             'time': 3.0, 'value': 6, 'transform': 'rate',
             'summarize': 'p50'},
        ]
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass', 'metrics': metrics,
                }],
            }],
        }

        summary = extract_result_metrics(data)[0]['short_json'][0]

        self.assertEqual(summary, {
            'name': 'custom.counter', 'type': 'scalar', 'host': 'local',
            'samples': 3, 'transform': 'rate',
            'summarize': 'p50',
            'summary': {'p50': 2.0},
            'value_count': 2,
        })

    def test_producer_metadata_drives_total_summary(self):
        metrics = [
            {'name': 'custom.drop.counter', 'type': 'scalar', 'host': 'local',
             'time': 1.0, 'value': 0, 'summarize': 'total'},
            {'name': 'custom.drop.counter', 'type': 'scalar', 'host': 'local',
             'time': 2.0, 'value': 3, 'summarize': 'total'},
            {'name': 'custom.drop.counter', 'type': 'scalar', 'host': 'local',
             'time': 3.0, 'value': 2, 'summarize': 'total'},
        ]
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass', 'metrics': metrics,
                }],
            }],
        }

        summary = extract_result_metrics(data)[0]['short_json'][0]

        self.assertEqual(summary, {
            'name': 'custom.drop.counter', 'type': 'scalar', 'host': 'local',
            'samples': 3, 'summarize': 'total',
            'summary': {'total': 5}, 'value_count': 3,
        })

    def test_full_json_preserves_samples(self):
        metric = {'name': 'throughput', 'type': 'scalar', 'time': 2,
                  'value': 1, 'host': 'local', 'summarize': 'p50'}
        sample = {'name': 'cpu.utilization', 'type': 'per-cpu',
                  'host': 'local', 'time': 1, 'value': [99.5, None],
                  'aggregation': 'busy-core-equivalents',
                  'summarize': 'p90'}
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass',
                    'metrics': [metric, sample],
                }],
            }],
        }

        rows = extract_result_metrics(data)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['full_json'], [metric, sample])
        cpu_summary = rows[0]['short_json'][1]
        self.assertEqual(cpu_summary, {
            'name': 'cpu.utilization', 'type': 'per-cpu',
            'host': 'local', 'samples': 1, 'value_count': 1,
            'aggregation': 'busy-core-equivalents', 'summarize': 'p90',
            'summary': {'p90': 0.995},
            'cpus': 2,
        })

    def test_unusable_metrics_skip_their_attempt(self):
        invalid = {
            'percentile': [{'name': 'x', 'value': 1, 'summarize': 'p150'}],
            'not-finite': [{'name': 'x', 'value': float('nan')}],
            'not-an-object': [1],
            'no-name': [{'value': 1}],
        }
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [
                    {'test': name, 'result': 'pass', 'metrics': metrics}
                    for name, metrics in invalid.items()
                ] + [{
                    'test': 'valid', 'result': 'pass',
                    'metrics': [{'name': 'x', 'value': 1,
                                 'summarize': 'p100'}],
                }],
            }],
        }

        rows = extract_result_metrics(data)

        self.assertEqual([row['subtest'] for row in rows], ['valid'])
        self.assertEqual(rows[0]['short_json'][0]['summary'], {'p100': 1})

    def test_values_which_are_not_numbers_are_left_out(self):
        metrics = [
            {'name': 'throughput', 'goal': 'higher', 'value': None},
            {'name': 'drops', 'goal': 'zero', 'value': True},
            {'name': 'latency', 'goal': 'lower', 'value': 'fast'},
            {'name': 'cpu', 'type': 'per-cpu', 'time': 1.0,
             'value': [1.0, 'busy']},
            {'name': 'rate', 'goal': 'higher', 'value': 10},
        ]
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass', 'metrics': metrics,
                }, {
                    'test': 'no-numbers', 'result': 'pass',
                    'metrics': [{'name': 'x', 'goal': 'higher',
                                 'value': None}],
                }],
            }],
        }

        rows = extract_result_metrics(data)

        self.assertEqual([row['subtest'] for row in rows], ['case'])
        self.assertEqual(rows[0]['full_json'], metrics)
        self.assertEqual(rows[0]['short_json'], [{
            'name': 'rate', 'goal': 'higher', 'samples': 1,
            'summarize': 'last', 'summary': {'last': 10},
            'regression_value': 10, 'value_count': 1,
        }])

    def test_shared_case_names_store_one_row_per_attempt(self):
        data = {
            'results': [{
                'group': 'group', 'test': 'test',
                'results': [{
                    'test': 'case', 'result': 'pass',
                    'metrics': [{'name': 'x', 'value': value}],
                } for value in (1, 2)],
            }],
        }

        rows = extract_result_metrics(data)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['full_json'], [{'name': 'x', 'value': 1}])


if __name__ == '__main__':
    unittest.main()
