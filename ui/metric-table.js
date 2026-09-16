let metric_table_runs = [];
let metric_table_data_source = null;

function metric_runner_name(run)
{
    return `${run.remote}:${run.executor}`;
}

function metric_case_name(run)
{
    return `${run.group}:${run.test}:${run.subtest}`;
}

function metric_run_date(run)
{
    return run.branch_date.slice(0, 10);
}

function metric_detail_data_source()
{
    const params = new URLSearchParams(window.location.search);
    const source = params.get('detail-data');
    if (source)
        return source;
    if (metric_table_data_source?.endsWith('result-metrics.json'))
        return metric_table_data_source.replace(/result-metrics\.json$/,
                                                'result-metric-details.json');
    return null;
}

function metric_detail_url(run)
{
    const params = new URLSearchParams();
    const source = metric_detail_data_source();

    params.set('id', run.id);
    if (source)
        params.set('data', source);
    return `metric-details.html?${params.toString()}`;
}

function metric_history_url(run)
{
    const params = new URLSearchParams();
    for (const key of ['remote', 'executor', 'group', 'test', 'subtest'])
        params.set(key, run[key]);
    params.set('branch', nipa_br_pfx_get(run.branch));
    if (metric_table_data_source)
        params.set('data', metric_table_data_source);
    const source = metric_detail_data_source();
    if (source)
        params.set('detail-data', source);
    return `metrics.html?${params.toString()}`;
}

function metric_run_is_newer(candidate, current)
{
    if (!current)
        return true;
    if (candidate.result === 'pass' && current.result !== 'pass')
        return true;
    if (candidate.result !== 'pass' && current.result === 'pass')
        return false;

    if (candidate.start !== current.start)
        return candidate.start > current.start;
    return candidate.id > current.id;
}

function metric_regression_results(run)
{
    return run.short_json
        .filter(metric => metric.regression_result)
        .map(metric => ({
            name: metric.name,
            host: metric.host || '',
            display_label: metric.aggregation || metric.display_label || '',
            ...metric.regression_result,
        }));
}

function metric_result_status(run)
{
    if (run.result !== 'pass')
        return 'regression';

    const regressions = metric_regression_results(run);
    if (regressions.some(result => result.status === 'regression' ||
                         result.status === 'error'))
        return 'regression';
    if (regressions.some(result => result.status === 'insufficient-data'))
        return 'insufficient';
    if (regressions.some(result => result.status === 'ok'))
        return 'ok';
    return 'untracked';
}

function metric_result_class(run)
{
    const suffix = {
        regression: '',
        insufficient: '-insufficient',
        ok: '-ok',
        untracked: '-untracked',
    }[metric_result_status(run)];
    return `metric-regression${suffix}`;
}

function metric_regression_tooltip(run)
{
    const lines = [];
    for (const result of metric_regression_results(run)) {
        const label = result.host ? `${result.name} / ${result.host}` :
            result.name;
        const unit = result.display_label ? ` ${result.display_label}` : '';
        if (result.status === 'insufficient-data') {
            lines.push(`${label}: insufficient history ` +
                       `(${result.baseline_samples} samples, ` +
                       `current ${result.current}${unit})`);
        } else if (result.status === 'error' ||
                   result.baseline === undefined) {
            lines.push(`${label}: ${result.status} ` +
                       `(current ${result.current}${unit}, must be zero)`);
        } else {
            const change = result.delta_percent === undefined ? '' :
                `, ${result.delta_percent.toFixed(1)}%`;
            lines.push(`${label}: ${result.status}${change} ` +
                       `(current ${result.current}${unit}, ` +
                       `baseline ${result.baseline}${unit})`);
        }
    }
    return lines;
}

function populate_metric_select(id, values)
{
    const select = document.getElementById(id);
    select.replaceChildren(new Option('-- all --', ''));
    for (const value of [...values].sort())
        select.add(new Option(value, value));
}

function populate_metric_filters()
{
    populate_metric_select('branch-filter', new Set(
        metric_table_runs.map(run => nipa_br_pfx_get(run.branch))));
    populate_metric_select('runner-filter', new Set(
        metric_table_runs.map(metric_runner_name)));
    document.getElementById('date-count').value = '30';
    document.getElementById('name-filter').value = '';
    for (const id of ['show-regression', 'show-ok', 'show-insufficient',
                      'show-untracked'])
        document.getElementById(id).checked = true;
    document.getElementById('sort-name').checked = true;

    const params = new URLSearchParams(window.location.search);
    const values = {
        'date-count': params.get('date-count'),
        'branch-filter': params.get('branch-filter'),
        'runner-filter': params.get('runner-filter'),
        'name-filter': params.get('name-filter'),
    };
    for (const [id, value] of Object.entries(values)) {
        if (value !== null)
            document.getElementById(id).value = value;
    }
    for (const id of ['show-regression', 'show-ok', 'show-insufficient',
                      'show-untracked']) {
        if (params.get(id) === '0')
            document.getElementById(id).checked = false;
    }
    if (params.get('sort') === 'regression')
        document.getElementById('sort-regression').checked = true;

    document.getElementById('metric-controls').disabled = false;
}

function metric_status_is_visible(status)
{
    return document.getElementById(`show-${status}`).checked;
}

function update_metric_table_url()
{
    const url = new URL(window.location.href);
    for (const id of ['date-count', 'branch-filter', 'runner-filter',
                      'name-filter', 'show-regression', 'show-ok',
                      'show-insufficient', 'show-untracked'])
        url.searchParams.delete(id);
    url.searchParams.delete('sort');

    const date_count = document.getElementById('date-count').value;
    const branch = document.getElementById('branch-filter').value;
    const runner = document.getElementById('runner-filter').value;
    const name = document.getElementById('name-filter').value;
    if (date_count !== '30')
        url.searchParams.set('date-count', date_count);
    if (branch)
        url.searchParams.set('branch-filter', branch);
    if (runner)
        url.searchParams.set('runner-filter', runner);
    if (name)
        url.searchParams.set('name-filter', name);
    for (const id of ['show-regression', 'show-ok', 'show-insufficient',
                      'show-untracked']) {
        if (!document.getElementById(id).checked)
            url.searchParams.set(id, '0');
    }
    if (document.getElementById('sort-regression').checked)
        url.searchParams.set('sort', 'regression');
    window.history.pushState({}, '', url);
}

function build_metric_table()
{
    const status = document.getElementById('metric-status');
    const table = document.getElementById('metric-table');
    const dates = new Set();
    const rows = new Map();
    const selected_branch = document.getElementById('branch-filter').value;
    const selected_runner = document.getElementById('runner-filter').value;
    const needle = document.getElementById('name-filter').value;
    let needle_re = null;
    let warning = '';
    if (needle) {
        try {
            needle_re = new RegExp(needle);
        } catch (error) {
            warning = `Bad test case regex: ${error.message}`;
        }
    }

    for (const run of metric_table_runs) {
        const date = metric_run_date(run);
        const branch = nipa_br_pfx_get(run.branch);
        const runner = metric_runner_name(run);
        const testcase = metric_case_name(run);
        if ((selected_branch && branch !== selected_branch) ||
            (selected_runner && runner !== selected_runner) ||
            (needle_re && !needle_re.test(testcase)))
            continue;

        const key = [branch, runner, testcase].join('\x1f');
        if (!rows.has(key))
            rows.set(key, {branch, runner, testcase, runs: new Map()});

        const row = rows.get(key);
        const current = row.runs.get(date);
        if (metric_run_is_newer(run, current))
            row.runs.set(date, run);
    }

    for (const [key, row] of rows) {
        for (const [date, run] of row.runs) {
            if (!metric_status_is_visible(metric_result_status(run)))
                row.runs.delete(date);
            else
                dates.add(date);
        }
        if (!row.runs.size)
            rows.delete(key);
    }

    table.replaceChildren();
    if (!rows.size || !dates.size) {
        table.hidden = true;
        status.hidden = false;
        status.textContent = warning || (metric_table_runs.length ?
            'No measurements match these filters.' :
            'No performance measurements have been collected yet.');
        return;
    }

    const date_count = Number.parseInt(
        document.getElementById('date-count').value, 10) || 30;
    const sorted_dates = [...dates].sort().reverse().slice(0, date_count);
    const status_order = {
        regression: 0, insufficient: 1, untracked: 2, ok: 3,
    };
    const sorted_rows = [...rows.values()]
        .filter(row => sorted_dates.some(date => row.runs.has(date)))
        .sort(function(a, b) {
            if (document.getElementById('sort-regression').checked) {
                const a_status = Math.min(...sorted_dates
                    .map(date => a.runs.get(date))
                    .filter(Boolean)
                    .map(run => status_order[metric_result_status(run)]));
                const b_status = Math.min(...sorted_dates
                    .map(date => b.runs.get(date))
                    .filter(Boolean)
                    .map(run => status_order[metric_result_status(run)]));
                if (a_status !== b_status)
                    return a_status - b_status;
            }
            return a.testcase.localeCompare(b.testcase) ||
                a.runner.localeCompare(b.runner) ||
                a.branch.localeCompare(b.branch);
        });

    const head = table.createTHead().insertRow();
    const case_header = document.createElement('th');
    case_header.textContent = 'Test case';
    head.appendChild(case_header);
    for (const date of sorted_dates) {
        const header = document.createElement('th');
        header.className = 'metric-date';
        header.textContent = date;
        head.appendChild(header);
    }

    const body = table.createTBody();
    for (const row_data of sorted_rows) {
        const row = body.insertRow();
        const name = row.insertCell();
        name.className = 'metric-case';

        const representative = row_data.runs.values().next().value;
        const link = document.createElement('a');
        link.href = metric_history_url(representative);
        link.textContent = row_data.testcase;
        name.appendChild(link);

        const dimensions = document.createElement('span');
        dimensions.className = 'metric-dimensions';
        dimensions.textContent = `${row_data.runner} · ${row_data.branch}`;
        name.appendChild(dimensions);

        for (const date of sorted_dates) {
            const cell = row.insertCell();
            const run = row_data.runs.get(date);
            if (!run)
                continue;

            cell.className = `metric-result ${metric_result_class(run)}`;
            const result_link = document.createElement('a');
            result_link.href = metric_detail_url(run);
            result_link.textContent = '●';
            const series_count = run.short_json.length;
            result_link.title = [
                run.branch,
                run.start,
                `${run.result}; ${series_count} metric series`,
                ...metric_regression_tooltip(run),
            ].filter(Boolean).join('\n');
            result_link.setAttribute('aria-label',
                `${row_data.testcase} on ${date}: ${run.result}`);
            cell.appendChild(result_link);
        }
    }

    status.hidden = !warning;
    status.textContent = warning;
    table.hidden = false;
}

// Links can name a data file, which must not come from another site
function metric_same_origin(source)
{
    try {
        return new URL(source, window.location.href).origin ===
            window.location.origin;
    } catch (error) {
        return false;
    }
}

async function load_metric_table()
{
    const status = document.getElementById('metric-status');
    const params = new URLSearchParams(window.location.search);
    metric_table_data_source = params.get('data');
    if (metric_table_data_source &&
        !metric_same_origin(metric_table_data_source)) {
        status.hidden = false;
        status.textContent = 'Not loading measurements from another site: ' +
            metric_table_data_source;
        return;
    }
    const url = metric_table_data_source || 'query/metrics';

    try {
        const response = await fetch(url);
        if (!response.ok)
            throw new Error(`HTTP ${response.status}`);
        metric_table_runs = await response.json();
        populate_metric_filters();
        build_metric_table();
    } catch (error) {
        status.hidden = false;
        status.textContent = `Failed to load measurements from ${url}: ${error}`;
    }
}

function do_it()
{
    for (const id of ['date-count', 'branch-filter', 'runner-filter',
                      'show-regression', 'show-ok', 'show-insufficient',
                      'show-untracked', 'sort-name', 'sort-regression']) {
        document.getElementById(id).addEventListener(
            'change', build_metric_table);
    }
    document.getElementById('name-filter').addEventListener(
        'input', build_metric_table);
    document.getElementById('update-url-button').addEventListener(
        'click', function(event) {
            event.preventDefault();
            update_metric_table_url();
        });
    window.addEventListener('popstate', function() {
        populate_metric_filters();
        build_metric_table();
    });
    load_metric_table();
}
