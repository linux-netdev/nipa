function load_times_series(data, patch_time)
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

    return entries;
}

function load_times(data, canva_id)
{
    let e1, e2;

    e1 = load_times_series(data, true);
    e2 = load_times_series(data, false);

    const ctx = document.getElementById(canva_id);

    new Chart(ctx, {
	type: 'scatter',
	data: {
	    labels: e1.map(function(e){return e.l;}),
	    datasets: [{
		backgroundColor: "rgba(0, 0, 0, 0)",
		pointBorderColor: "rgba(0, 64, 255, 0.7)",
		label: 'By patch post time',
		data: e1.map(function(e){return e.v;})
	    }, {
		backgroundColor: "rgba(0, 0, 0, 0)",
		pointBorderColor: "rgba(255, 64, 0, 0.7)",
		label: 'By check delivery time',
		data: e2.map(function(e){return e.v;})
	    }]
	},
	options: {
	    plugins: {
                title: {
                    display: true,
                    text: 'Patch processing times over last 7 days',
                    padding: 0
                },
		legend: {
		    position: 'chartArea',
		},
	    },
	    scales: {
		y: {
		    type: 'linear',
		    title: {
		        display: true,
		        text: 'Hours',
		        padding: 0
                    },
		    ticks: {
		        stepSize: 3
		    },
		    suggestedMax: 12,
		    beginAtZero: true
		},
		x: {
		    type: 'linear',
		    title: {
		        display: true,
		        text: 'Hours ago',
		        padding: 0
                    },
		    ticks: {
		        stepSize: 24
		    },
		    suggestedMax: 12,
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

    load_times(data, 'process-time');
}

function colorify_str_any(value, color_map)
{
    if (!(value in color_map))
	return value;
    return '<span style="color:' + color_map[value] + '">' + value + '</span>';
}

function colorify_basic(value)
{
    return colorify_str_any(value, {"fail": "red",
				    "pass": "green",
				    "pending": "#809fff"});
}

function colorify_str(value, good)
{
    if (value == good) {
	ret = '<span style="color:green">';
    } else {
	ret = '<span style="color:red">';
    }
    return ret + value + '</span>';
}

function systemd_add_one(table, system, sname, v)
{
    var row = table.insertRow();
    var name = row.insertCell(0);
    var ss = row.insertCell(1);
    var tasks = row.insertCell(2);
    var cpu = row.insertCell(3);
    var mem = row.insertCell(4);

    let sstate = "";
    let now = system["time-mono"];

    if (v.TriggeredBy == 0) {
	cpuSec = v.CPUUsageNSec / 1000;
	cpuHours = cpuSec / (now - v.ExecMainStartTimestampMonotonic);
	cpuHours = cpuHours.toFixed(2);

	memGb = (v.MemoryCurrent / (1024 * 1024 * 1024)).toFixed(2);
	memGb = memGb + 'GB';

	state = v.ActiveState + " / " + v.SubState;
	sstate = colorify_str(state, "active / running");

	taskcnt = v.TasksCurrent;
    } else {
	cpuSec = v.CPUUsageNSec / 1000;
	cpuHours = cpuSec / (v.ExecMainExitTimestampMonotonic -
			     v.ExecMainStartTimestampMonotonic);
	cpuHours = cpuHours.toFixed(2);

	sstate = colorify_str(v.Result, "success");

	taskcnt = '';
	memGb = '';
    }

    name.innerHTML = sname;
    ss.innerHTML = sstate;
    ss.setAttribute("style", "text-align: center");
    tasks.innerHTML = taskcnt;
    tasks.setAttribute("style", "text-align: right");
    cpu.innerHTML = cpuHours;
    cpu.setAttribute("style", "text-align: right");
    mem.innerHTML = memGb;
    mem.setAttribute("style", "text-align: right");
}

function systemd(data_raw, data_local, data_remote)
{
    var table = document.getElementById("systemd");

    $.each(data_local, function(i, v) {
	systemd_add_one(table, data_raw, i, v);
    });

    $.each(data_remote, function(name, remote) {
	$.each(remote["services"], function(service, v) {
	    systemd_add_one(table, remote, name + "/" + service, v);
	});
    });
}

function load_runners(data_raw)
{
    var table = document.getElementById("runners");

    $.each(data_raw, function(i, v) {
	var row = table.insertRow();
	let cell_id = 0;
	var name = row.insertCell(cell_id++);
	var qlen = row.insertCell(cell_id++);
	var tid = row.insertCell(cell_id++);
	var test = row.insertCell(cell_id++);
	var pid = row.insertCell(cell_id++);
	var patch = row.insertCell(cell_id++);

	name.innerHTML = i.slice(0, -6);
	pid.innerHTML = v.progress;
	patch.innerHTML = v.patch;
	tid.innerHTML = v["test-progress"];
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
    systemd(data_raw, data_raw["services"], data_raw["remote"]);
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
            var full = Math.floor(msec) + convs[i - 1][1];
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

function avg_time_e(avgs, v)
{
    const ent_name = v.remote + '/' + v.executor;

    if (!(ent_name in avgs))
	return 0;
    return avgs[ent_name]["min-dly"] +
	avgs[ent_name]["sum"] / avgs[ent_name]["cnt"];
}

function wrap_link(objA, objB, text)
{
    let url = null;

    if ("link" in objA)
	url = objA.link;
    else if ("link" in objB)
	url = objB.link;
    else
	return text;

    return "<a href=\"" + url + "\">" + text + "</a>";
}

function load_fails(data_raw)
{
    var fail_table = document.getElementById("recent-fails");
    var crash_table = document.getElementById("recent-crashes");

    $.each(data_raw, function(idx0, v) {
	$.each(v.results, function(idx1, r) {
	    if (r.result != "pass" && nipa_pw_reported(v, r)) {
		let i = 0, row = fail_table.insertRow();
		row.insertCell(i++).innerHTML = v.branch;
		row.insertCell(i++).innerHTML = v.remote;
		row.insertCell(i++).innerHTML = r.test;
		row.insertCell(i++).innerHTML = colorify_basic(r.result);
		if ("retry" in r)
		    row.insertCell(i++).innerHTML = colorify_basic(r.retry);
	    }

	    if ("crashes" in r) {
		for (crash of r.crashes) {
		    let i = 0, row = crash_table.insertRow();
		    row.insertCell(i++).innerHTML = wrap_link(r, v, r.test);
		    row.insertCell(i++).innerHTML = crash;
		}
	    }
	});
    });
}

function add_summaries(table, summary, reported)
{
    let row = table.insertRow();
    let i = 0;

    let cell = row.insertCell(i++); // branch
    cell.innerHTML = "summary";

    cell = row.insertCell(i++);     // remote
    let count_line = summary["remote-cnt"] + " remotes";
    if (summary["hidden"]) {
	if (summary["hidden"] == summary["remote-cnt"])
	    count_line += " (all hidden)";
	else
	    count_line += " (" + summary["hidden"] + " hidden)";
    }

    cell.innerHTML = count_line;

    cell = row.insertCell(i++);     // time
    cell.innerHTML = msec_to_str(summary["time-pass"]);

    let str_psf = {"str": "", "overall": ""};

    colorify_str_psf(str_psf, "fail", summary["fail"], "red");
    colorify_str_psf(str_psf, "skip", summary["skip"], "#809fff");
    colorify_str_psf(str_psf, "pass", summary["total"], "green");

    var link_to_contest = "<a href=\"contest.html?pw-n=0&";
    link_to_contest += "branch=" + summary["branch"];
    if (reported)
	link_to_contest += "&pw-n=0";
    else
	link_to_contest += "&pw-y=0";
    link_to_contest += "\">" + str_psf.str + "</a>";

    cell = row.insertCell(i++);     // tests
    cell.innerHTML = link_to_contest;

    cell = row.insertCell(i++);     // result
    cell.setAttribute("style", "text-align: left; font-weight: bold; font-style: normal;");
    cell.innerHTML = colorify_basic(branch_results[summary.branch]);

    row.setAttribute("class", "summary-row");
}

function reset_summary(summary)
{
    summary["branch"] = null;
    summary["remote-cnt"] = 0;
    summary["time-pass"] = 0;
    summary["total"] = 0;
    summary["skip"] = 0;
    summary["fail"] = 0;
    summary["hidden"] = 0;
}

function load_result_table_one(data_raw, table, reported, avgs)
{
    const summarize = document.getElementById("contest-summary").checked;
    let summary = {};

    reset_summary(summary);

    $.each(data_raw, function(i, v) {
	var pass = 0, skip = 0, fail = 0, total = 0, ignored = 0;
	var link = v.link;
	$.each(v.results, function(i, r) {
	    if (nipa_pw_reported(v, r) != reported) {
		ignored++;
		return 1;
	    }

	    if (r.result == "pass") {
		pass++;
	    } else if (r.result == "skip") {
		skip++;
	    } else {
		fail++;
	    }

	    total++;
	    if (!link)
		link = r.link;
	});

	if (!total && ignored && v.executor != "brancher")
	    return 1;

	var t_start = new Date(v.start);
	var t_end = new Date(v.end);

	if (v.remote == "brancher") {
	    summary["branch"] = v.branch;
	    add_summaries(table, summary, reported);
	    reset_summary(summary);
	} else {
	    summary["total"] += total;
	    if (total) {
		summary["remote-cnt"] += 1;
		if (summary["time-pass"] < t_end - t_start)
		    summary["time-pass"] = t_end - t_start;
	    }

	    summary["skip"] += skip;
	    summary["fail"] += fail;
	    if (summarize && total && total == pass) {
		summary["hidden"] += 1;
		return 1;
	    }
	}

	var str_psf = {"str": "", "overall": ""};

	colorify_str_psf(str_psf, "fail", fail, "red");
	colorify_str_psf(str_psf, "skip", skip, "#809fff");
	colorify_str_psf(str_psf, "pass", pass, "green");

	const span_small = " <span style=\"font-size: small;\">(";
	if (ignored) {
	    if (reported)
		str_psf.overall += span_small + "ignored: " + ignored + ")</span>";
	    else
		str_psf.overall += span_small + "reported: " + ignored + ")</span>";
	}

	var row = table.insertRow();

	var branch = row.insertCell(0);
	var remote = row.insertCell(1);

	    var a = "<a href=\"" + link + "\">";

	if (v.remote != "brancher") {
	    var time = row.insertCell(2);

	    if (link)
		remote.innerHTML = a + v.remote + "</a>";
	    else
		remote.innerHTML = v.remote;
	    if (total) {
		var cnt = row.insertCell(3);
		var res = row.insertCell(4);

		var link_to_contest = "<a href=\"contest.html?";
		link_to_contest += "branch=" + v.branch;
		link_to_contest += "&executor=" + v.executor;
		if (reported)
		    link_to_contest += "&pw-n=0";
		else
		    link_to_contest += "&pw-y=0";
		link_to_contest += "\">";

		cnt.innerHTML = link_to_contest + str_psf.str + "</a>";
		res.innerHTML = str_psf.overall;
		time.innerHTML = msec_to_str(t_end - t_start);
	    } else {
		var pend;

		const passed = Date.now() - v.start;
		const expect = Math.round(avg_time_e(avgs, v));
		var remain = expect - passed;
		var color = "pink";

		if (v.end == 0) {
		    pend = "no result";
		    if (passed > 1000 * 60 * 15 /* 15 min */)
			color = "red";
		    else
			color = "#809fff";
		} else if (remain > 0) {
		    pend = "pending (expected in " + (msec_to_str(remain)).toString() + ")";
		    color = "#809fff";
		} else if (remain < -1000 * 60 * 60 * 2) { /* 2 h */
		    pend = "timeout";
		} else {
		    pend = "pending (expected " + (msec_to_str(-remain)).toString() + " ago)";
		}
		time.innerHTML = "<span style=\"font-style: italic; color: " + color + "\">" + pend + "</span>";
		time.setAttribute("colspan", "3");
	    }
	} else {
	    let res = row.insertCell(2);
	    let br_pull = "";

	    if (v.start)
		remote.innerHTML = v.start.toLocaleString();
	    else
		remote.innerHTML = "unknown";
	    remote.setAttribute("colspan", "2");
	    if (v.pull_status != "okay")
		br_pull = " (pull: " + v.pull_status + ")";
	    branch.innerHTML = a + v.branch + "</a>" + br_pull;
	    branch.setAttribute("colspan", "2");
	    res.innerHTML = "";
	    row.setAttribute("class", "end-row");
	}
    });
}

function rem_exe(v)
{
    return v.remote + "/" + v.executor;
}

function load_result_table(data_raw, reload)
{
    var table = document.getElementById("contest");
    var table_nr = document.getElementById("contest-purgatory");
    var branch_pull_status = {};
    var branch_start = {};

    // Parse branch info to extract pull status
    $.each(branches_info, function(i, v) {
	let summary = null;
	$.each(v['base-pulls'], function(url, res) {
	    if (res == "okay" && !summary) {
		summary = res;
	    } else if (res == "resolved" && (!summary || summary == "okay")) {
		summary = res;
	    } else {
		summary = res;
	    }
	});
	branch_pull_status[i] = summary;
    });

    // Decorate branchers and collect branch_start
    $.each(data_raw, function(i, v) {
	v.start = new Date(v.start);
	v.end = new Date(v.end);

	branches.add(v.branch);

	if (v.remote == "brancher") {
            branch_start[v.branch] = v.start;
	    v.pull_status = branch_pull_status[v.branch];
	}
    });

    // Continue with only 6 most recent branches
    let recent_branches = new Set(Array.from(branches).sort().slice(-10));
    data_raw = $.grep(data_raw,
		      function(v, i) { return recent_branches.has(v.branch); });

    // Calculate expected runtimes
    var avgs = {};
    $.each(data_raw, function(i, v) {
	if (!v.results)
	    return 1;

	const ent_name = v.remote + '/' + v.executor;

	if (!(ent_name in avgs))
	    avgs[ent_name] = {"cnt": 0, "sum": 0, "min-dly": 0};
	avgs[ent_name]["cnt"] += 1;
	avgs[ent_name]["sum"] += (v.end - v.start);

	if (v.branch in branch_start) {
	    const dly = v.start - branch_start[v.branch];
	    const old = avgs[ent_name]["min-dly"];

	    if (!old || old > dly)
		avgs[ent_name]["min-dly"] = dly;
	}
    });

    // Fill in runs for "AWOL" executors
    let known_execs = {};
    let branch_execs = {};
    for (v of data_raw) {
	let re = rem_exe(v);

	if (!(v.branch in branch_execs))
	    branch_execs[v.branch] = new Set();
	branch_execs[v.branch].add(re);

	if (!(re in known_execs))
	    known_execs[re] = {
		"executor": v.executor,
		"remote" : v.remote,
		"branches" : new Set()
	    };
	known_execs[re].branches.add(v.branch);
    }

    let known_exec_set = new Set(Object.keys(known_execs));
    for (br of recent_branches) {
	for (re of known_exec_set) {
	    if (branch_execs[br].has(re))
		continue;

	    data_raw.push({
		"executor" : known_execs[re].executor,
		"remote" : known_execs[re].remote,
		"branch" : br,
		"start" : branch_start[br],
		"end" : 0,
	    });
	}
    }

    // Sort & display
    data_raw.sort(function(a, b){
	if (b.branch != a.branch)
	    return b.branch > a.branch ? 1 : -1;

	// fake entry for "no result" always up top
	if (b.end === 0)
	    return 1;

	// both pending, sort by expected time
	if (a.results == null && b.results == null)
	    return avg_time_e(avgs, b) - avg_time_e(avgs, a);
	// pending before not pending
	if (b.results == null)
	    return 1;
	if (a.results == null)
	    return -1;

	return b.end - a.end;
    });

    $("#contest tr").slice(1).remove();
    $("#contest-purgatory tr").slice(1).remove();
    load_result_table_one(data_raw, table, true, avgs);
    load_result_table_one(data_raw, table_nr, false, avgs);
    if (!reload)
	load_fails(data_raw);
}

let xfr_todo = 4;
let all_results = null;
let branches_info = null;
let branches = new Set();
let branch_results = {};

function reload_results()
{
    load_result_table(all_results, true);
}

function loaded_one()
{
    if (!--xfr_todo) {
	load_result_table(all_results, false);

	let summary_checkbox = document.getElementById("contest-summary");
	summary_checkbox.addEventListener("change", reload_results);
    }
}

function results_loaded(data_raw)
{
    all_results = data_raw;
    loaded_one();
}

function branch_res_doit(data_raw)
{
    $.each(data_raw, function(i, v) {
	branch_results[i] = v.result;
    });

    loaded_one();
}

function add_one_test_filter_hdr(keys_present, key, hdr, row)
{
    if (!keys_present.has(key))
	return ;

    let th = document.createElement("th");
    th.innerHTML = hdr;
    row.appendChild(th);
}

function add_one_test_filter(keys_present, key, v, i, row)
{
    if (!keys_present.has(key))
	return 0;

    let cell = row.insertCell(i);
    if (key in v)
	cell.innerHTML = v[key];
    return 1;
}

function filters_doit(data_raw)
{
    let cf_crashes = document.getElementById("cf-crashes");
    let cf_execs = document.getElementById("cf-execs");
    let cf_tests = document.getElementById("cf-tests");
    var output, sep = "";

    output = "<b>Remotes reported:</b> ";
    $.each(data_raw.remotes, function(i, v) {
	output += sep + v;
	sep = ", ";
    });
    cf_execs.innerHTML = output;

    let keys_present = new Set();
    $.each(data_raw["ignore-results"], function(i, v) {
	for (const k of Object.keys(v))
	    keys_present.add(k);
    });

    let cf_tests_hdr = document.getElementById("cf-tests-hdr");
    add_one_test_filter_hdr(keys_present, "remote", "Remote", cf_tests_hdr);
    add_one_test_filter_hdr(keys_present, "executor", "Executor", cf_tests_hdr);
    add_one_test_filter_hdr(keys_present, "branch", "Branch", cf_tests_hdr);
    add_one_test_filter_hdr(keys_present, "group", "Group", cf_tests_hdr);
    add_one_test_filter_hdr(keys_present, "test", "Test", cf_tests_hdr);

    $.each(data_raw["ignore-results"], function(_i, v) {
	let row = cf_tests.insertRow();
	let i = 0;

	i += add_one_test_filter(keys_present, "remote", v, i, row);
	i += add_one_test_filter(keys_present, "executor", v, i, row);
	i += add_one_test_filter(keys_present, "branch", v, i, row);
	i += add_one_test_filter(keys_present, "group", v, i, row);
	i += add_one_test_filter(keys_present, "test", v, i, row);
    });

    output = "<b>Crashes ignored:</b><br />";
    $.each(data_raw["ignore-crashes"], function(i, v) {
	output += v + "<br />";
    });
    cf_crashes.innerHTML = output;

    nipa_set_filters_json(data_raw);
    loaded_one();
}

function branches_loaded(data_raw)
{
    branches_info = data_raw;
    loaded_one();
}

function do_it()
{
    /*
     * Please remember to keep these assets in sync with `scripts/ui_assets.sh`
     */
    $(document).ready(function() {
        $.get("static/nipa/checks.json", run_it)
    });
    $(document).ready(function() {
        $.get("static/nipa/systemd.json", status_system)
    });
    $(document).ready(function() {
        $.get("contest/filters.json", filters_doit)
    });
    $(document).ready(function() {
        $.get("static/nipa/branch-results.json", branch_res_doit)
    });
    $(document).ready(function() {
        $.get("query/results?branches=10", results_loaded)
    });
    $(document).ready(function() {
        $.get("static/nipa/branches-info.json", branches_loaded)
    });
}
