# SPDX-License-Identifier: GPL-2.0

"""Helpers for extracting and summarizing metrics from a NIPA result."""

import collections
import copy
import json
import math
import re
import statistics


def _series_key(metric):
    dimensions = {
        key: value for key, value in metric.items()
        if key not in ('time', 'value')
    }
    return json.dumps(dimensions, sort_keys=True, separators=(',', ':'))


def _nearest_rank(values, percentile):
    rank = max(1, math.ceil(percentile * len(values)))
    return values[rank - 1]


def _percentile(values, operation):
    percentile = float(operation[1:])
    if not 0 <= percentile <= 100:
        raise ValueError(f'percentile out of range: {operation}')
    return _nearest_rank(values, percentile / 100)


def _summary_number(value):
    return round(value, 6) if isinstance(value, float) else value


def _per_cpu_rate_samples(observations):
    observations.sort(key=lambda observation: (observation[1]['time'],
                                                observation[0]))
    samples = []
    for (_prev_index, previous), (_index, current) in zip(
            observations, observations[1:]):
        elapsed = current['time'] - previous['time']
        if elapsed <= 0:
            continue
        rates = [
            (new - old) / elapsed
            for old, new in zip(previous['value'], current['value'])
            if old is not None and new is not None and new >= old
        ]
        if rates:
            samples.append(rates)
    return samples


def _scalar_rate_values(observations):
    observations.sort(key=lambda observation: (observation[1]['time'],
                                                observation[0]))
    return [
        (current['value'] - previous['value']) /
        (current['time'] - previous['time'])
        for (_prev_index, previous), (_index, current) in zip(
            observations, observations[1:])
        if current['time'] > previous['time'] and
        current['value'] >= previous['value']
    ]


def _per_cpu_percent_totals(observations):
    return [
        sum(value for value in metric['value'] if value is not None) / 100
        for _index, metric in observations
    ]


# Units of the metrics the selftests report, for those which do not tell
_METRIC_UNITS = {
    'throughput': 'Gbps',
}


def _is_number(value):
    # bool is an int, NaN and Infinity have no place in strict JSON
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, int)


def _usable(metric, metric_type):
    """Whether an observation has a value of its type to summarize."""
    value = metric.get('value')
    if metric_type == 'per-cpu':
        return isinstance(value, list) and \
            all(item is None or _is_number(item) for item in value)
    return _is_number(value)


def summarize_metrics(metrics):
    """Return one summary per metric series.

    Metrics of the selftests carry a name, a value, a goal and maybe a unit,
    else NIPA may know theirs by name; scalars with their last value as the
    summary, unless the metric says otherwise, as the system metrics of NIPA
    do. Tests may print anything, values which are not finite numbers are
    left out.
    """
    series = {}
    for index, metric in enumerate(metrics):
        key = _series_key(metric)
        if key not in series:
            dimensions = {
                name: value for name, value in metric.items()
                if name not in ('time', 'value')
            }
            series[key] = {
                'dimensions': dimensions,
                'observations': [],
            }
        series[key]['observations'].append((index, metric))

    summaries = []
    for entry in series.values():
        dimensions = entry['dimensions']
        metric_type = dimensions.get('type', 'scalar')
        observations = [
            (index, metric) for index, metric in entry['observations']
            if _usable(metric, metric_type)
        ]
        if not isinstance(dimensions.get('name'), str) or not observations:
            continue
        base = dimensions.copy()
        base['samples'] = len(observations)

        transform = base.get('transform')
        aggregation = base.get('aggregation')
        operation = base.setdefault('summarize', 'last')
        # A unit the test reports wins over the one known for the name
        unit = base.get('unit')
        if not isinstance(unit, str):
            unit = _METRIC_UNITS.get(base['name'])
        if unit and 'display_label' not in base:
            base['display_label'] = unit

        if metric_type == 'scalar':
            if transform == 'rate':
                values = _scalar_rate_values(observations)
            else:
                values = [metric['value']
                          for _index, metric in observations]
        elif metric_type == 'per-cpu':
            if transform == 'rate':
                rate_samples = _per_cpu_rate_samples(observations)
                if aggregation == 'busy-core-equivalents':
                    values = [sum(sample) for sample in rate_samples]
                else:
                    values = [value for sample in rate_samples
                              for value in sample]
            elif aggregation == 'busy-core-equivalents':
                values = _per_cpu_percent_totals(observations)
            else:
                values = [
                    value
                    for _index, metric in observations
                    for value in metric['value']
                    if value is not None
                ]
            base['cpus'] = max(len(metric['value'])
                               for _index, metric in observations)
        else:
            continue

        if not values:
            continue
        last_value = values[-1]
        values.sort()
        if operation and operation[0] == 'p':
            base['summary'] = {
                operation: _summary_number(_percentile(values, operation)),
            }
        elif operation == 'max':
            base['summary'] = {'max': _summary_number(values[-1])}
        elif operation == 'last':
            base['summary'] = {'last': _summary_number(last_value)}
        elif operation == 'total':
            base['summary'] = {'total': _summary_number(sum(values))}
        else:
            continue

        if base.get('goal') in ('higher', 'lower', 'zero'):
            base['regression_value'] = base['summary'][operation]

        base['value_count'] = len(values)
        summaries.append(base)

    return summaries


_BRANCH_DATE = r'[0-9]{4}-[0-9]{2}-[0-9]{2}--[0-9]{2}-[0-9]{2}'
# Date stamp of NIPA branches, valid in Python and PostgreSQL regexes
BRANCH_DATE_SUFFIX = rf'-{_BRANCH_DATE}$'
_BRANCH_DATE_SUFFIX_RE = re.compile(BRANCH_DATE_SUFFIX)


def branch_prefix(branch):
    return _BRANCH_DATE_SUFFIX_RE.sub('', branch)


def branch_pattern(prefix):
    return rf'^{re.escape(prefix)}-{_BRANCH_DATE}$'


def _regression_series_key(row, metric):
    dimensions = {
        key: value for key, value in metric.items()
        if key not in {
            'summary', 'samples', 'value_count', 'cpus', 'display_range',
            'display_scale', 'display_label', 'regression_value',
            'regression_result'}
    }
    identity = {
        'branch_prefix': branch_prefix(row['branch']),
        'remote': row['remote'],
        'executor': row['executor'],
        'group': row['group'],
        'test': row['test'],
        'subtest': row['subtest'],
        'metric': dimensions,
    }
    return json.dumps(identity, sort_keys=True, separators=(',', ':'))


# How much worse than the median of its recent results a performance metric
# has to get to regress
REGRESSION_TOLERANCE = 0.1
# How many earlier results the median is taken of
REGRESSION_WINDOW = 5


def evaluate_regressions(rows, *, window=REGRESSION_WINDOW):
    """Annotate compact metric rows using earlier comparable results.

    The goal of a metric gives its direction: an error metric must be zero,
    a performance metric regresses when it is worse than the median of its
    recent results by more than REGRESSION_TOLERANCE. NIPA owns the
    historical cohort: successful results from the same branch family,
    runner, test case, and metric series. Input ordering is preserved in the
    returned deep copy.
    """
    evaluated = copy.deepcopy(rows)
    history = {}
    ordered = sorted(enumerate(evaluated),
                     key=lambda item: (item[1]['start'], item[0]))

    for _index, row in ordered:
        if row['result'] != 'pass':
            continue
        for metric in row['short_json']:
            goal = metric.get('goal')
            if goal not in ('higher', 'lower', 'zero'):
                continue

            try:
                current = metric['regression_value']
                # Anything else in the history would stop all later checks
                if not _is_number(current):
                    continue
                if goal == 'zero':
                    metric['regression_result'] = {
                        'status': 'error' if current else 'ok',
                        'current': _summary_number(current),
                    }
                    continue
                key = _regression_series_key(row, metric)
                baseline_values = history.setdefault(key, [])[-window:]
                result = {
                    'status': 'insufficient-data',
                    'current': _summary_number(current),
                    'baseline_samples': len(baseline_values),
                }
                if baseline_values:
                    baseline = statistics.median(baseline_values)
                    threshold = REGRESSION_TOLERANCE * abs(baseline)
                    if goal == 'higher':
                        worsening = baseline - current
                    else:
                        worsening = current - baseline
                    result.update({
                        'status': ('regression'
                                   if worsening > threshold else 'ok'),
                        'baseline': _summary_number(baseline),
                        'threshold': _summary_number(threshold),
                    })
                    if baseline:
                        result['delta_percent'] = _summary_number(
                            (current - baseline) / abs(baseline) * 100)
                metric['regression_result'] = result
                history[key].append(current)
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue

    return evaluated


def _case_key(row):
    return (branch_prefix(row['branch']), row['remote'], row['executor'],
            row['group'], row['test'], row['subtest'])


def evaluate_recent_regressions(rows, limit, *, window=REGRESSION_WINDOW):
    """Evaluate rows, then return the latest `limit` results of each test case.

    The older rows only serve as the baseline: given `window` of them for
    each test case, even the oldest returned result is compared with a full
    window of history.
    """
    evaluated = evaluate_regressions(rows, window=window)
    latest = sorted(enumerate(evaluated),
                    key=lambda item: (item[1]['start'], item[0]),
                    reverse=True)
    counts = collections.Counter()
    keep = set()
    for index, row in latest:
        key = _case_key(row)
        counts[key] += 1
        if counts[key] <= limit:
            keep.add(index)
    return [row for index, row in enumerate(evaluated) if index in keep]


def extract_result_metrics(data):
    """Return one summarized/full metric record per test-case attempt."""
    rows = []
    # Distinct test cases may share a sanitized name, keep the first one
    stored = set()

    for test in data.get('results') or []:
        group = test.get('group')
        test_name = test.get('test')
        if not group or not test_name:
            continue

        for case in test.get('results') or []:
            subtest = case.get('test')
            if not subtest:
                continue

            base = {
                'group': group,
                'test': test_name,
                'subtest': subtest,
                'link': test.get('link'),
            }
            attempts = (
                (0, case.get('result'), case.get('metrics', [])),
                (1, case.get('retry'), case.get('retry_metrics', [])),
            )
            for attempt, result, metrics in attempts:
                if not isinstance(result, str) or not isinstance(metrics, list):
                    continue
                result = result.lower()
                if result not in ('pass', 'fail', 'skip'):
                    continue

                key = (group, test_name, subtest, attempt)
                if not metrics or key in stored:
                    continue
                # Metrics come from the tests, which may print anything
                try:
                    summary = summarize_metrics(metrics)
                    # Strict JSON consumers, like the result database,
                    # reject NaN and Infinity
                    json.dumps([summary, metrics], allow_nan=False)
                except (AttributeError, LookupError, TypeError, ValueError,
                        ArithmeticError):
                    continue
                if not summary:
                    continue

                stored.add(key)
                row = base.copy()
                row.update({
                    'short_json': summary,
                    'full_json': metrics,
                    'attempt': attempt,
                    'result': result,
                })
                rows.append(row)

    return rows
