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

function systemd(data_raw)
{
    var table = document.getElementById("systemd");

    $.each(data_raw, function(i, v) {
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

	name.innerHTML = i;
	as.innerHTML = astate;
	ss.innerHTML = sstate;
	res.innerHTML = result;
	tasks.innerHTML = taskcnt;
	cpu.innerHTML = cpuHours;
	mem.innerHTML = memGb;
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
    systemd(data_raw["services"]);
    load_runners(data_raw["runners"]);
    load_runtime(data_raw["log-files"]);
}

function do_it()
{
    $(document).ready(function() {
        $.get("static/nipa/checks.json", run_it)
    });
    $(document).ready(function() {
        $.get("static/nipa/systemd.json", status_system)
    });
}
