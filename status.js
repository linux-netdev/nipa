function load_times(data, canva_id, patch_time)
{
    const minute = 1000 * 60;
    const hour = minute * 60;
    const day = hour * 24;
    const year = day * 365;

    var entries = [];
    var prev_min = 0;
    var prev_val = 0;

    var offset = new Date().getTimezoneOffset() * minute;
    var now = Date.now() + offset;

    $.each(data, function(i, v) {
	if (v["check-date"] == null)
	    return true;

	var p_date = new Date(v["date"]);
	var c_date = new Date(v["check-date"]);

	if (patch_time) {
	    minutes_back = v.minutes_back;
	} else {
	    minutes_back = Math.round((now - c_date) / minute);
	}
	if (minutes_back / (64 * 24) > 7)
	    return true;

	value = ((c_date - p_date) / hour).toFixed(2);

	if (Math.abs(prev_min - minutes_back) > 2 ||
	    Math.abs(prev_val - value) > 0.02) {
	    entries.push({"l": (minutes_back / 60).toFixed(2), "v": Math.max(value, 0)});

	    prev_min = minutes_back;
	    prev_val = value;
	}
    });

    // Sort by labels
    entries.sort(function(a, b){return a.l - b.l;});

    const ctx = document.getElementById(canva_id);

    new Chart(ctx, {
	type: 'line',
	data: {
	    labels: entries.map(function(e){return e.l;}),
	    datasets: [{
		tension: 0.1,
		label: 'Patch age at check delivery',
		data: entries.map(function(e){return e.v;})
	    }]
	},
	options: {
	    scales: {
		y: {
		    type: 'linear',
		    ticks: {
		        stepSize: 3
		    },
		    beginAtZero: true
		},
		x: {
		    type: 'linear',
		    ticks: {
		        stepSize: 24
		    },
		    reverse: true
		}
	    }
	}
    });
}

function run_it(data_raw)
{
    const minute = 1000 * 60;
    const hour = minute * 60;
    const day = hour * 24;
    const year = day * 365;

    var offset = new Date().getTimezoneOffset() * minute;
    var now = Date.now() + offset;

    var latest = new Date(data_raw[0].date);
    var data = [];
    $.each(data_raw, function(i, v) {
	var date = new Date(v.date);
	if (latest < date)
	    latest = date;

	if (v.check != "build_clang")
	    return true;

	v.days_back = Math.round((now - date) / day) + 1;
	v.minutes_back = Math.round((now - date) / minute) + 1;

	data.push(v);
    });

    load_times(data, 'process-time', false);
    load_times(data, 'process-time-p', true);
}

function colorify_str(value, good)
{
    if (value == good) {
	ret = '<p style="color:green">';
    } else {
	ret = '<p style="color:red">';
    }
    return ret + value + '</p>';
}

function systemd_add_one(table, sname, v)
{
    var row = table.insertRow();
    var name = row.insertCell(0);
    var as = row.insertCell(1);
    var ss = row.insertCell(2);
    var res = row.insertCell(3);
    var tasks = row.insertCell(4);
    var cpu = row.insertCell(5);
    var mem = row.insertCell(6);

    if (v.TriggeredBy == 0) {
	cpuSec = v.CPUUsageNSec / 1000000000;
	cpuHours = (cpuSec / (60 * 60)).toFixed(0);
	cpuHours = cpuHours + ' hours';

	memGb = (v.MemoryCurrent / (1024 * 1024 * 1024)).toFixed(2);
	memGb = memGb + 'GB';

	astate = colorify_str(v.ActiveState, "active");
	sstate = colorify_str(v.SubState, "running");

	result = v.Result;

	taskcnt = v.TasksCurrent;
    } else {
	cpuSec = v.CPUUsageNSec / 1000000000;
	cpuHours = cpuSec.toFixed(2);
	cpuHours = cpuHours + ' sec';

	result = colorify_str(v.Result, "success");

	astate = '';
	sstate = '';
	taskcnt = '';
	memGb = '';
    }

    name.innerHTML = sname;
    as.innerHTML = astate;
    ss.innerHTML = sstate;
    res.innerHTML = result;
    tasks.innerHTML = taskcnt;
    cpu.innerHTML = cpuHours;
    mem.innerHTML = memGb;
}

function systemd(data_raw, data_remote)
{
    var table = document.getElementById("systemd");

    $.each(data_raw, function(i, v) {
	systemd_add_one(table, i, v);
    });

    $.each(data_remote, function(name, remote) {
	$.each(remote["services"], function(service, v) {
	    systemd_add_one(table, name + "/" + service, v);
	});
    });
}

function load_runners(data_raw)
{
    var table = document.getElementById("runners");

    $.each(data_raw, function(i, v) {
	var row = table.insertRow();
	var name = row.insertCell(0);
	var qlen = row.insertCell(1);
	var pid = row.insertCell(2);
	var patch = row.insertCell(3);
	var test = row.insertCell(4);

	name.innerHTML = i;
	pid.innerHTML = v.progress;
	patch.innerHTML = v.patch;
	test.innerHTML = v.test;
	qlen.innerHTML = v.backlog;
    });
}

function load_runtime(data_raw)
{
    var entries = [];

    $.each(data_raw["data"], function(i, v) {
	entries.push({"l": i, "v": v});
    });

    entries.sort(function(a, b){return b.v.pct - a.v.pct;});

    const ctx = document.getElementById("run-time");

    new Chart(ctx, {
	type: 'bar',
	data: {
	    labels: entries.map(function(e){return e.l;}),
	    datasets: [{
		yAxisID: 'A',
		label: 'Percent of total runtime',
		borderRadius: 5,
		data: entries.map(function(e){return e.v.pct;}),
	    }, {
		yAxisID: 'B',
		label: 'Avgerage runtime in sec',
		borderWidth: 1,
		borderRadius: 5,
		data: entries.map(function(e){return e.v.avg;})
	    }]
	},
	options: {
	    responsive: true,
	    plugins: {
		legend: {
		    position: 'bottom',
		},
		title: {
		    display: true,
		    text: 'Check runtime'
		}
	    },
	    scales: {
		A: {
		    display: true,
		    beginAtZero: true
		},
		B: {
		    position: 'right',
		    display: true,
		    beginAtZero: true
		}
	    },
	},
    });
}

function status_system(data_raw)
{
    systemd(data_raw["services"], data_raw["remote"]);
    load_runners(data_raw["runners"]);
    load_runtime(data_raw["log-files"]);
}

function msec_to_str(msec) {
    const convs = [
        [1, "ms"],
        [1000, "s"],
        [60, "m"],
        [60, "h"],
        [24, "d"],
        [7, "w"]
    ];

    if (msec <= 0)
	return msec.toString();

    for (i = 0; i < convs.length; i++) {
        if (msec < convs[i][0]) {
            var full = Math.round(msec) + convs[i - 1][1];
            if (i > 1) {
                var frac = Math.round(msec * convs[i - 1][0] % convs[i - 1][0]);
                if (frac)
                    full += " " + frac + convs[i - 2][1];
            }
            return full;
        }
        msec /= convs[i][0];
    }

    return "TLE";
}

function colorify_str_psf(str_psf, name, value, color)
{
    var bspan = '<span style="color: white; background-color:' + color + '">';
    var cspan = '<span style="color:' + color + '">';

    if (value && str_psf.overall == "")
	str_psf.overall = cspan + name + '</span>';

    if (str_psf.str != "") {
	str_psf.str = " / " + str_psf.str;
    }

    var p;
    if (value == 0) {
	p = value;
    } else {
	p = bspan + value + '</span>';
    }
    str_psf.str = p + str_psf.str;
}

function load_result_table(data_raw)
{
    var table = document.getElementById("contest");

    $.each(data_raw, function(i, v) {
	v.start = new Date(v.start);
	v.end = new Date(v.end);
    });

    var avgs = {};
    $.each(data_raw, function(i, v) {
	if (!(v.executor in avgs))
	    avgs[v.executor] = {"cnt": 0, "sum": 0};
	avgs[v.executor]["cnt"] += 1;
	avgs[v.executor]["sum"] += (v.end - v.start);
    });

    data_raw.sort(function(a, b){return b.end - a.end;});
    data_raw = data_raw.slice(0, 75);

    $.each(data_raw, function(i, v) {
	var row = table.insertRow();

	var branch = row.insertCell(0);
	var remote = row.insertCell(1);

	var pass = 0, skip = 0, warn = 0, fail = 0, total = 0;
	var link = v.link;
	$.each(v.results, function(i, r) {
	    if (r.result == "pass") {
		pass++;
	    } else if (r.result == "skip") {
		skip++;
	    } else if (r.result == "warn") {
		warn++;
	    } else if (r.result == "fail") {
		fail++;
	    }

	    total++;
	    if (!link)
		link = r.link;
	});
	var str_psf = {"str": "", "overall": ""};

	colorify_str_psf(str_psf, "fail", fail, "red");
	colorify_str_psf(str_psf, "warn", warn, "orange");
	colorify_str_psf(str_psf, "skip", skip, "blue");
	colorify_str_psf(str_psf, "pass", pass, "green");

	    var t_start = new Date(v.start);
	    var t_end = new Date(v.end);
	    var a = "<a href=\"" + link + "\">";

	if (v.remote != "brancher") {
	    var time = row.insertCell(2);

	    remote.innerHTML = a + v.remote + "</a>";
	    if (total) {
		var cnt = row.insertCell(3);
		var res = row.insertCell(4);

		cnt.innerHTML = str_psf.str;
		res.innerHTML = str_psf.overall;
		time.innerHTML = msec_to_str(t_end - t_start);
	    } else {
		var pend;

		const passed = Date.now() - v.start;
		const expect = Math.round(avgs[v.executor]["sum"] / avgs[v.executor]["cnt"]);
		var remain = expect - passed;

		if (remain > 0) {
		    pend = "pending (expected in " + (msec_to_str(remain)).toString() + ")";
		} else if (remain < -1000 * 60 * 60 * 2) { /* 2 h */
		    pend = "timeout";
		} else {
		    pend = "pending (expected" + (msec_to_str(-remain)).toString() + " ago)";
		}
		time.innerHTML = "<span style=\"font-style: italic; color: blue\">" + pend + "</span>";
		time.setAttribute("colspan", "3");
	    }
	} else {
	    var res = row.insertCell(2);

	    remote.innerHTML = v.start.toLocaleString();
	    remote.setAttribute("colspan", "2");
	    branch.innerHTML = a + v.branch + "</a>";
	    branch.setAttribute("colspan", "2");
	    res.innerHTML = str_psf.overall;
	}
    });
}

function results_doit(data_raw)
{
    load_result_table(data_raw);
}

function do_it()
{
    $(document).ready(function() {
        $.get("static/nipa/checks.json", run_it)
    });
    $(document).ready(function() {
        $.get("static/nipa/systemd.json", status_system)
    });
    $(document).ready(function() {
        $.get("contest/all-results.json", results_doit)
    });
}
