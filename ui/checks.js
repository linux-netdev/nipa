function load_color(data, canva_id, state_flt, check_flt)
{
    var per_patch = {};
    $.each(data, function(i, v) {
	if (state_flt && v.state != state_flt)
	    return true;
	if (check_flt && v.check != check_flt)
	    return true;
	if (!per_patch[v.id]) {
	    per_patch[v.id] = { g: "0", "y": 0, "r": 0, "date": v.date };
	    per_patch[v.id].days_back = v.days_back;
	    per_patch[v.id].state = v.state;
	}
	if (v.result == "success") {
	    per_patch[v.id].g += 1;
	} else if (v.result == "fail") {
	    per_patch[v.id].r += 1;
	} else if (v.result == "warning") {
	    per_patch[v.id].y += 1;
	} else {
	    console.log("Bad patch result", v);
	}
    });

    var max_back = 0;
    $.each(per_patch, function(i, v) {
	if (v) {
	    if (v.days_back > max_back)
		max_back = v.days_back;
	}
    });
    var labels = [];
    var per_day = [];
    for (let i = 0; i < max_back + 1; i++) {
	labels.push(i);
	per_day[i] = { "g": 0, "y": 0, "r": 0 };
    }
    $.each(per_patch, function(i, v) {
	if (v) {
	    idx = labels.length - v.days_back - 1;
	    labels[idx] = v.date.substring(0, 10);
	    pd = per_day[idx];
	    if (!pd) {
		console.log(v);
		console.log(per_day);
		return true;
	    }
	    if (v.r) {
		pd.r += 1;
	    } else if (v.y) {
		pd.y += 1;
	    } else if (v.g) {
		pd.g += 1;
	    } else {
		console.log("Bad per_patch", v);
	    }
	}
    });
    for (let i = 0; i < max_back + 1; i++) {
	var total = per_day[i].r + per_day[i].y + per_day[i].g;
	per_day[i].pr = per_day[i].r * 100 / total;
	per_day[i].py = per_day[i].y * 100 / total;
	per_day[i].pg = per_day[i].g * 100 / total;
    }

    const ctx = document.getElementById(canva_id);

    new Chart(ctx, {
	type: 'line',
	data: {
	    labels: labels,
	    datasets: [{
		borderColor: 'green',
		tension: 0.1,
		label: 'Green',
		data: per_day.map(function(v) {return v.g}),
		borderWidth: 1,
		borderDash: [1, 4]
	    }, {
		borderColor: 'goldenrod',
		tension: 0.1,
		label: 'Yellow',
		data: per_day.map(function(v) {return v.y}),
		borderWidth: 1,
		borderDash: [1, 4]
	    }, {
		borderColor: 'red',
		tension: 0.1,
		label: 'Red',
		data: per_day.map(function(v) {return v.r}),
		borderWidth: 1,
		borderDash: [1, 4]
	    }, {
		borderColor: 'red',
		tension: 0.1,
		label: 'Pct Red',
		data: per_day.map(function(v) {return v.pr}),
		borderWidth: 1
	    }, {
		borderColor: 'gold',
		tension: 0.1,
		label: 'Pct Yellow',
		data: per_day.map(function(v) {return v.py}),
		borderWidth: 1
	    }, {
		borderColor: 'green',
		tension: 0.1,
		label: 'Pct Green',
		data: per_day.map(function(v) {return v.pg}),
		borderWidth: 3
	    }]
	},
	options: {
	    scales: {
		y: {
		    beginAtZero: true
		}
	    }
	}
    });
}

function load_pc(data, canva_id, state_flt)
{
    var reds = new Set();
    var yels = new Set();
    var max_back = 0;
    $.each(data, function(i, v) {
	if (state_flt && v.state != state_flt)
	    return true;

	if (v.result == "fail") {
	    reds.add(v.check);
	} else if (v.result == "warning") {
	    yels.add(v.check);
	}
	if (v.days_back > max_back)
	    max_back = v.days_back;
    });

    var labels = [];
    var per_day = [];
    for (let i = 0; i < max_back + 1; i++) {
	labels.push(i);
	per_day[i] = {};
	reds.forEach(function(v){
	    per_day[i][v + " - fail"] = 0;
	    per_day[i].total = 0;
	});
	yels.forEach(function(v){
	    per_day[i][v + " - warn"] = 0;
	    per_day[i][v + " - warn"].total = 0;
	});
    }

    $.each(data, function(i, v) {
	if (state_flt && v.state != state_flt)
	    return true;

	idx = labels.length - v.days_back - 1;
	labels[idx] = v.date.substring(0, 10);
	pd = per_day[idx];

	pd.total += 1;
	if (v.result == "fail") {
	    pd[v.check + " - fail"] += 1;
	} else if (v.result == "warning") {
	    pd[v.check + " - warn"] += 1;
	}
    });

    lines = [];
    reds.forEach(function(v){lines.push(
	{
	    tension: 0.1,
	    label: v + " - fail",
	    data: per_day.map(function(pd) {
		return pd[v + " - fail"] * 100 / pd.total;
	    }),
	    borderWidth: 1
	}
    );});
    yels.forEach(function(v){lines.push(
	{
	    tension: 0.1,
	    label: v + " - warn",
	    data: per_day.map(function(pd) {
		return pd[v + " - warn"] * 100 / pd.total;
	    }),
	    borderWidth: 1,
	    borderDash: [6, 10]
	}
    );});

    const ctx = document.getElementById(canva_id);

    new Chart(ctx, {
	type: 'line',
	data: {
	    labels: labels,
	    datasets: lines
	},
	options: {
	    scales: {
		y: {
		    beginAtZero: true
		}
	    }
	}
    });
}

function load_outputs(data)
{
    var table = document.getElementById("logTable");

    var top_out = [];
    var top_out_cnt = {};
    $.each(data, function(i, v) {
	if (v.result != "success") {
	    if (top_out_cnt[v.description]) {
		top_out_cnt[v.description]++;
	    } else {
		top_out.push(v);
		top_out_cnt[v.description] = 1;
	    }
	}
    });

    top_out.sort(function(a, b) {
	return top_out_cnt[b.description] - top_out_cnt[a.description];
    });

    for (let i = 0; i < 20; i++) {
	var v = top_out[i];

	var row = table.insertRow();
	var check = row.insertCell(0);
	var output = row.insertCell(1);
	var hits = row.insertCell(2);

	check.innerHTML = v.check;
	output.innerHTML = v.description;
	hits.innerHTML = top_out_cnt[v.description];
    }
}

function __lar_acc_stats(v, k, stats, day_lim)
{
    if (v.days_back > day_lim)
	return;

    if (!stats[k]) {
	stats[k] = {
	    "check": v.check,
	    "author": v.author,
	    "author_id": v.author_id,
	    "success": 0,
	    "fail": 0,
	    "warning": 0,
	    "total": 0
	};
    }

    stats[k].total += 1;
    stats[k][v.result] += 1;
}

function load_avg_rate_table(data, table_id, state_flt)
{
    var stats2w = {}, statsAll = {};

    $.each(data, function(i, v) {
	if (state_flt && v.state != state_flt)
	    return true;

	__lar_acc_stats(v, v.check, stats2w, 14);
	__lar_acc_stats(v, v.check, statsAll, 999);
    });

    all_stats = [];
    $.each(statsAll, function(i, v) { all_stats.push(v); });
    all_stats.sort(function(b, a) {
	if (a.fail != b.fail)
	    return a.fail - b.fail;
	return a.warning - b.warning;
    });

    var table = document.getElementById(table_id);

    $.each(all_stats, function(i, v) {
	var row = table.insertRow();
	var check = row.insertCell(0);
	var s1 = row.insertCell(1);
	var w1 = row.insertCell(2);
	var f1 = row.insertCell(3);
	var s2 = row.insertCell(4);
	var w2 = row.insertCell(5);
	var f2 = row.insertCell(6);

	check.innerHTML = v.check;
	if (stats2w[v.check]) {
	    v2 = stats2w[v.check];
	    s1.innerHTML = Math.round(v2.success * 100 / v2.total) + "%";
	    w1.innerHTML = Math.round(v2.warning * 100 / v2.total) + "%";
	    f1.innerHTML = Math.round(v2.fail * 100 / v2.total) + "%";
	}
	s2.innerHTML = Math.round(v.success * 100 / v.total)+ "%";
	w2.innerHTML = Math.round(v.warning * 100 / v.total)+ "%";
	f2.innerHTML = Math.round(v.fail * 100 / v.total)+ "%";
    });
}

function load_person_table(data, table_id, state_flt)
{
    var stats = {};

    $.each(data, function(i, v) {
	if (state_flt && v.state != state_flt)
	    return true;

	__lar_acc_stats(v, v.author, stats, 30);
    });

    all_stats = [];
    $.each(stats, function(i, v) { all_stats.push(v); });
    all_stats.sort(function(b, a) {
	if (a.fail != b.fail)
	    return a.fail - b.fail;
	return a.warning - b.warning;
    });

    var table = document.getElementById(table_id);

    $.each(all_stats, function(i, v) {
	if (i > 15 || (!v.fail && !v.warning))
	    return false;
	var row = table.insertRow();
	var idx = row.insertCell(0);
	var author = row.insertCell(1);
	var fail = row.insertCell(2);
	var warn = row.insertCell(3);
	var total = row.insertCell(4);

	base = "https://patchwork.kernel.org/project/netdevbpf/list/";
	ref = "?state=*&submitter=" + v.author_id;
	author_name = `<a href="${base}${ref}">` + v.author + '</a>';

	idx.innerHTML = i;
	author.innerHTML = author_name;
	warn.innerHTML = v.warning;
	fail.innerHTML = v.fail;
	total.innerHTML = v.total;
    });
}

function run_it(data_raw)
{
    const minute = 1000 * 60;
    const hour = minute * 60;
    const day = hour * 24;
    const year = day * 365;

    var latest = new Date(data_raw[0].date);
    var data = [];
    $.each(data_raw, function(i, v) {
	var date = new Date(v.date);
	if (latest < date)
	    latest = date;

	if (v.check.indexOf("vmtest-bpf") != -1)
	    return true;
	if (v.state == "awaiting-upstream" ||
	    v.state == "not-applicable" ||
	    v.state == "deferred")
	    return true;

	v.days_back = Math.round((Date.now() - date) / day) + 1;

	data.push(v);
    });

    var status = document.getElementById("status_line");
    var discards = " (discards: " + (data_raw.length - data.length) + ")";
    status.innerHTML = "Rows: " + data.length + discards + " Latest: " + latest;

    load_color(data, 'gyr_accept', "accepted", false);
    load_color(data, 'gyr_all', false, false);

    load_pc(data, 'pc_accept', "accepted");
    load_pc(data, 'pc_all', false);

    load_avg_rate_table(data, "avg_rate_accept", "accepted");
    load_avg_rate_table(data, "avg_rate_all", false);

    load_person_table(data, "person_accept", "accepted");
    load_person_table(data, "person_all", false);

    load_outputs(data);
    load_color(data, 'cc_maintainers', "accepted", "cc_maintainers");
}

function do_it()
{
    /*
     * Please remember to keep these assets in sync with `scripts/ui_assets.sh`
     */
    $(document).ready(function() {
        $.get("static/nipa/checks.json", run_it)
    });
}
