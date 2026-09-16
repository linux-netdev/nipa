let metric_result = null;

function select_metric_result(payload, id)
{
    return Array.isArray(payload) ?
        payload.find(entry => String(entry.id) === id) : payload;
}

function metric_host_compare(a, b)
{
    const order = {local: 0, remote: 1};
    const a_order = order[a || ''] ?? 2;
    const b_order = order[b || ''] ?? 2;
    return a_order - b_order || (a || '').localeCompare(b || '');
}

function metric_sort(a, b)
{
    if (a.name !== b.name)
        return a.name.localeCompare(b.name);
    return metric_host_compare(a.host, b.host);
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

// Results come from remote runners, follow their web links only
function metric_web_link(link)
{
    if (!link)
        return null;
    try {
        const url = new URL(link, window.location.href);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch (error) {
        return null;
    }
}

function add_metadata_row(table, label, value, link = null)
{
    const row = table.insertRow();
    const heading = document.createElement('th');
    heading.scope = 'row';
    heading.textContent = label;
    row.appendChild(heading);
    const cell = row.insertCell();
    if (link) {
        const anchor = document.createElement('a');
        anchor.href = link;
        anchor.textContent = value;
        cell.appendChild(anchor);
    } else {
        cell.textContent = value;
    }
    return cell;
}

function render_value_metadata(table)
{
    // Metrics of the selftests have one value, rather than samples to chart
    for (const metric of metric_result.short_json) {
        if (!metric.goal)
            continue;

        const host = metric.host ? ` / ${metric.host}` : '';
        const unit = metric.display_label ? ` ${metric.display_label}` : '';
        add_metadata_row(table, `${metric.name}${host}`,
                         `${metric.summary[metric.summarize]}${unit}`);
    }
}

function render_regression_metadata(table)
{
    for (const metric of metric_result.short_json) {
        const result = metric.regression_result;
        if (!result)
            continue;

        const host = metric.host ? ` / ${metric.host}` : '';
        let text;
        if (result.status === 'insufficient-data') {
            text = `Insufficient history (${result.baseline_samples} samples)`;
        } else if (result.status === 'error') {
            text = `error: ${result.current}, must be zero`;
        } else if (result.baseline === undefined) {
            text = `${result.status}: ${result.current}`;
        } else {
            const delta = result.delta_percent === undefined ? '' :
                `, ${result.delta_percent.toFixed(1)}%`;
            text = `${result.status}: ${result.current} versus ` +
                `${result.baseline}${delta}`;
        }
        const cell = add_metadata_row(
            table, `Regression (${metric.name}${host})`, text);
        if (result.status === 'regression' || result.status === 'error') {
            cell.style.backgroundColor = '#d06060';
            cell.style.color = 'white';
        }
    }
}

function render_metadata()
{
    const table = document.getElementById('metric-metadata');
    table.replaceChildren();
    add_metadata_row(table, 'Branch', metric_result.branch);
    add_metadata_row(table, 'Runner',
                     `${metric_result.remote}:${metric_result.executor}`);
    add_metadata_row(table, 'Test case',
                     `${metric_result.group}:${metric_result.test}:` +
                     metric_result.subtest);
    add_metadata_row(table, 'Started',
                     new Date(metric_result.start).toLocaleString());
    add_metadata_row(table, 'Result', metric_result.result);
    add_metadata_row(table, 'Attempt', metric_result.attempt.toString());
    render_value_metadata(table);
    render_regression_metadata(table);
    const output = metric_web_link(metric_result.link);
    if (output)
        add_metadata_row(table, 'Output', 'Open test output', output);
    table.hidden = false;
    document.title = `${metric_result.test}:${metric_result.subtest} metrics`;
}

function metric_dimensions(metric)
{
    const dimensions = {};
    for (const [key, value] of Object.entries(metric)) {
        if (!['time', 'value'].includes(key))
            dimensions[key] = value;
    }
    return dimensions;
}

function rate_observations(observations)
{
    const rates = [];
    for (let index = 1; index < observations.length; index++) {
        const previous = observations[index - 1];
        const current = observations[index];
        const elapsed = current.time - previous.time;
        if (elapsed <= 0)
            continue;

        let values;
        if (Array.isArray(current.value)) {
            values = current.value.map(function(value, cpu) {
                const old_value = previous.value[cpu];
                if (value === null || old_value === null || value < old_value)
                    return null;
                return (value - old_value) / elapsed;
            });
        } else {
            if (current.value < previous.value)
                continue;
            values = (current.value - previous.value) / elapsed;
        }
        rates.push({time: current.time, value: values});
    }
    return rates;
}

function scale_observations(observations, scale)
{
    return observations.map(function(observation) {
        const value = Array.isArray(observation.value) ?
            observation.value.map(item => item === null ?
                null : item * scale) : observation.value * scale;
        return {time: observation.time, value: value};
    });
}

function sampled_metric_groups()
{
    const groups = new Map();
    for (const metric of metric_result.full_json) {
        if (metric.time === undefined)
            continue;
        const dimensions = metric_dimensions(metric);
        const key = JSON.stringify(Object.entries(dimensions).sort());
        if (!groups.has(key)) {
            groups.set(key, {
                ...dimensions,
                observations: [],
            });
        }
        groups.get(key).observations.push({
            time: metric.time,
            value: metric.value,
        });
    }

    return [...groups.values()]
        .map(function(group) {
            group.observations.sort((a, b) => a.time - b.time);
            if (group.transform === 'rate')
                group.observations = rate_observations(group.observations);
            const display_scale = group.display_scale ?? 1;
            if (display_scale !== 1) {
                group.observations = scale_observations(
                    group.observations, display_scale);
            }
            return group;
        })
        .filter(group => group.observations.length)
        .sort(metric_sort);
}

function series_color(index, count)
{
    const hue = Math.round(index * 360 / count);
    return `hsl(${hue}, 70%, 48%)`;
}

function per_cpu_datasets(group)
{
    let cpu_count = 0;
    for (const observation of group.observations)
        cpu_count = Math.max(cpu_count, observation.value.length);

    const values_by_cpu = [];
    for (let cpu = 0; cpu < cpu_count; cpu++) {
        const values = group.observations.map(
            observation => observation.value[cpu] ?? null);
        const present = values.filter(value => value !== null);
        if (present.length)
            values_by_cpu.push({cpu: cpu, values: values,
                                peak: Math.max(...present)});
    }
    values_by_cpu.sort((a, b) => b.peak - a.peak || a.cpu - b.cpu);

    return {
        total: values_by_cpu.length,
        datasets: values_by_cpu.map(function(series) {
            // Keep a CPU's color stable across every metric and host chart.
            const color = series_color(series.cpu, cpu_count);
            return {
                label: `CPU ${series.cpu}`,
                data: series.values,
                borderColor: color,
                backgroundColor: color,
                borderWidth: 1.5,
                pointRadius: 1,
                pointHoverRadius: 5,
                spanGaps: false,
                tension: 0,
            };
        }),
    };
}

function scalar_datasets(group)
{
    return {
        total: 1,
        datasets: [{
            label: group.host || group.name,
            data: group.observations.map(observation => observation.value),
            borderColor: '#3366cc',
            backgroundColor: '#3366cc',
            borderWidth: 2,
            pointRadius: 3,
            pointHoverRadius: 6,
            spanGaps: false,
            tension: 0,
        }],
    };
}

function render_sampled_chart(container, group)
{
    const series = group.type === 'per-cpu' ?
        per_cpu_datasets(group) : scalar_datasets(group);
    if (!series.datasets.length)
        return false;

    const box = document.createElement('div');
    box.className = 'metric-chart-box';
    const canvas = document.createElement('canvas');
    box.appendChild(canvas);
    container.appendChild(box);

    const dimensions = [group.origin, group.host].filter(Boolean).join(' / ');
    const dimension_label = dimensions ? ` / ${dimensions}` : '';
    const cpu_count = group.type === 'per-cpu' ?
        ` (${series.total} CPUs)` : '';
    const metric_name = group.transform === 'rate' ?
        `${group.name} rate` : group.name;
    const display_range = group.display_range || {};
    const title = `${metric_name}${dimension_label}${cpu_count}`;
    new Chart(canvas, {
        type: 'line',
        data: {
            labels: group.observations.map(observation => observation.time),
            datasets: series.datasets,
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {mode: 'nearest', intersect: false},
            plugins: {
                legend: {
                    display: series.total <= 32,
                    position: 'bottom',
                },
                title: {
                    display: true,
                    text: title,
                },
                tooltip: {
                    callbacks: {
                        afterBody: function(items) {
                            const time = items[0].label;
                            return `Time: ${time} seconds into test`;
                        },
                    },
                },
            },
            scales: {
                x: {title: {display: true, text: 'Seconds into test'}},
                y: {
                    min: display_range.min,
                    max: display_range.max,
                    title: {
                        display: true,
                        text: group.display_label || metric_name,
                    },
                },
            },
        },
    });
    return true;
}

function render_sampled_charts()
{
    const container = document.getElementById('sampled-charts');
    container.replaceChildren();
    const groups = sampled_metric_groups();
    let rendered = 0;
    for (const group of groups) {
        if (render_sampled_chart(container, group))
            rendered++;
    }
    document.getElementById('sample-section').hidden = rendered === 0;
    return rendered;
}

async function load_metric_details()
{
    const status = document.getElementById('metric-status');
    const params = new URLSearchParams(window.location.search);
    const data_source = params.get('data');
    const id = params.get('id');
    let url;
    if (data_source && !metric_same_origin(data_source)) {
        status.hidden = false;
        status.textContent = 'Not loading metric details from another ' +
            `site: ${data_source}`;
        return;
    } else if (data_source) {
        url = data_source;
    } else if (id) {
        url = `query/metrics/${encodeURIComponent(id)}`;
    } else {
        status.hidden = false;
        status.textContent = 'No metric result was selected.';
        return;
    }

    try {
        const response = await fetch(url);
        if (!response.ok)
            throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        metric_result = select_metric_result(payload, id);
        render_metadata();
        render_sampled_charts();
        status.textContent = '';
        status.hidden = true;
    } catch (error) {
        status.hidden = false;
        status.textContent = `Failed to load metric details from ${url}: ${error}`;
    }
}

function do_it()
{
    load_metric_details();
}
