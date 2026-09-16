let metric_runs = [];
let metric_data_source = null;

const chart_colors = [
    '#3366cc', '#dc3912', '#109618', '#ff9900', '#990099',
    '#0099c6', '#dd4477', '#66aa00', '#b82e2e', '#316395'
];
// URL parameters selecting the test case and runner of the history
const metric_case_keys = ['remote', 'executor', 'group', 'test', 'subtest'];

function runner_name(row)
{
    return `${row.remote}:${row.executor}`;
}

function case_name(row)
{
    return `${row.group}:${row.test}:${row.subtest}`;
}

function selected_case(params)
{
    const selected = {};
    for (const key of metric_case_keys) {
        if (!params.get(key))
            return null;
        selected[key] = params.get(key);
    }
    return selected;
}

function filtered_runs()
{
    const params = new URLSearchParams(window.location.search);
    const selected_branch = params.get('branch');
    const selected = selected_case(params);

    if (!selected)
        return [];

    return metric_runs.filter(function(run) {
        return run.result === 'pass' &&
            metric_case_keys.every(key => run[key] === selected[key]) &&
            (!selected_branch ||
             nipa_br_pfx_get(run.branch) === selected_branch);
    });
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

function metric_query_url(params)
{
    const query = new URLSearchParams();
    for (const key of metric_case_keys) {
        if (params.get(key))
            query.set(key, params.get(key));
    }
    if (params.get('branch'))
        query.set('br-pfx', params.get('branch'));
    return `query/metrics?${query.toString()}`;
}

function metric_summary_label(metric)
{
    const name = metric.transform === 'rate' ?
        `${metric.name} rate` : metric.name;
    if (metric.aggregation)
        return `${name} (${metric.aggregation}, ` +
            `${metric.summarize.toUpperCase()})`;
    if (metric.summarize !== 'last')
        return `${name} (${metric.summarize.toUpperCase()})`;
    return name;
}

function summary_metric_rows(runs)
{
    const rows = [];

    for (const run of runs) {
        for (const metric of run.short_json) {
            const aggregated = Boolean(metric.aggregation);
            const value = metric.summary[metric.summarize];
            rows.push({
                ...run,
                metric: metric.name,
                metric_label: metric_summary_label(metric),
                host: metric.host || '',
                origin: metric.origin || '',
                display_range: aggregated ? {} : metric.display_range,
                display_label: aggregated ? null : metric.display_label,
                summary: metric.summarize,
                samples: metric.samples,
                value: aggregated ? value :
                    value * (metric.display_scale ?? 1),
            });
        }
    }
    return rows;
}

function series_label(rows, row)
{
    const origins = new Set(rows.map(entry => entry.origin));
    return [origins.size > 1 ? row.origin : '', row.host]
        .filter(Boolean).join(' / ');
}

function metric_groups(rows)
{
    const groups = new Map();

    for (const row of rows) {
        const key = row.metric_label;
        if (!groups.has(key))
            groups.set(key, []);
        groups.get(key).push(row);
    }

    return [...groups.values()].sort((a, b) =>
        a[0].metric.localeCompare(b[0].metric) ||
        a[0].metric_label.localeCompare(b[0].metric_label));
}

function metric_host_compare(a, b)
{
    const order = {local: 0, remote: 1};
    const a_order = order[a] ?? 2;
    const b_order = order[b] ?? 2;
    return a_order - b_order || a.localeCompare(b);
}

function metric_detail_url(row)
{
    const current = new URLSearchParams(window.location.search);
    const params = new URLSearchParams({id: row.id});
    const overview_data = current.get('data') || metric_data_source;
    const detail_data = current.get('detail-data') ||
        overview_data?.replace(/result-metrics\.json$/,
                               'result-metric-details.json');
    if (detail_data)
        params.set('data', detail_data);
    return `metric-details.html?${params.toString()}`;
}

function render_one_chart(container, rows)
{
    const labels = [...new Set(rows.map(row => row.branch))];
    const branch_dates = new Map(rows.map(row =>
        [row.branch, row.branch_date]));
    const series = new Map();
    for (const row of rows) {
        const key = [row.origin, row.host].join('\x1f');
        if (!series.has(key))
            series.set(key, new Map());
        series.get(key).set(row.branch, row);
    }

    const datasets = [];
    let color_index = 0;
    const sorted_series = [...series.values()].sort(function(a, b) {
        const first_a = a.values().next().value;
        const first_b = b.values().next().value;
        return first_a.origin.localeCompare(first_b.origin) ||
            metric_host_compare(first_a.host, first_b.host);
    });
    for (const points of sorted_series) {
        const first = points.values().next().value;
        const color = chart_colors[color_index++ % chart_colors.length];
        const point_rows = labels.map(branch => points.get(branch) || null);
        datasets.push({
            label: series_label(rows, first),
            data: point_rows.map(row => row ? row.value : null),
            metricRows: point_rows,
            borderColor: color,
            backgroundColor: color,
            borderWidth: 2,
            pointRadius: 3,
            pointHoverRadius: 6,
            pointHitRadius: 12,
            spanGaps: false,
            tension: 0,
        });
    }

    const metric_label = rows[0].metric_label;
    const display_label = rows[0].display_label;
    const display_range = rows[0].display_range || {};
    const box = document.createElement('div');
    box.className = 'metric-chart-box';
    const canvas = document.createElement('canvas');
    box.appendChild(canvas);
    container.appendChild(box);

    new Chart(canvas, {
        type: 'line',
        data: {labels: labels, datasets: datasets},
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {mode: 'nearest', intersect: true},
            plugins: {
                legend: {position: 'bottom'},
                title: {display: true, text: metric_label},
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            const row = context.dataset.metricRows[context.dataIndex];
                            return `${context.dataset.label}: ${row.value}`;
                        },
                        afterBody: function(items) {
                            const item = items[0];
                            const row = item.dataset.metricRows[item.dataIndex];
                            return [
                                `Summary: ${row.summary}`,
                                `Samples: ${row.samples}`,
                                `Branch: ${row.branch}`,
                                `Run: ${new Date(row.start).toLocaleString()}`,
                            ];
                        },
                    },
                },
            },
            scales: {
                x: {
                    title: {display: true, text: 'Branch'},
                    ticks: {
                        callback: function(value) {
                            return branch_dates.get(
                                this.getLabelForValue(value));
                        },
                        maxRotation: 60,
                        minRotation: 30,
                    },
                },
                y: {
                    min: display_range.min,
                    max: display_range.max,
                    title: {
                        display: true,
                        text: display_label || metric_label,
                    },
                },
            },
            onClick: function(event, points, chart) {
                if (!points.length)
                    return;
                const point = points[0];
                const row = chart.data.datasets[point.datasetIndex]
                    .metricRows[point.index];
                window.location.href = metric_detail_url(row);
            },
        },
    });
}

function render_metric_charts()
{
    const runs = filtered_runs();
    const rows = summary_metric_rows(runs);
    const status = document.getElementById('metric-status');
    const container = document.getElementById('metric-charts');

    container.replaceChildren();

    if (!runs.length) {
        status.hidden = false;
        status.textContent = metric_runs.length ?
            'No measurements match these filters.' :
            'No performance measurements have been collected yet.';
        return;
    }

    const groups = metric_groups(rows);
    for (const group of groups)
        render_one_chart(container, group);

    status.textContent = '';
    status.hidden = true;
}

async function load_metrics()
{
    const status = document.getElementById('metric-status');
    status.hidden = false;
    status.textContent = 'Loading...';

    const url_params = new URLSearchParams(window.location.search);
    metric_data_source = url_params.get('data');
    if (metric_data_source && !metric_same_origin(metric_data_source)) {
        status.textContent = 'Not loading measurements from another site: ' +
            metric_data_source;
        return;
    }
    // Static sources hold every test case, the backend can select one
    const url = metric_data_source || metric_query_url(url_params);

    try {
        const response = await fetch(url);
        if (!response.ok)
            throw new Error(`HTTP ${response.status}`);
        metric_runs = await response.json();
        const selected = selected_case(url_params);
        const title = document.getElementById('metric-title');
        if (selected) {
            title.textContent =
                `${case_name(selected)} - ${runner_name(selected)}`;
        }
        render_metric_charts();
    } catch (error) {
        status.hidden = false;
        status.textContent = `Failed to load measurements from ${url}: ${error}`;
    }
}

function do_it()
{
    load_metrics();
}
