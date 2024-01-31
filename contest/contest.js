function colorify_str(value)
{
    if (value == "pass") {
	ret = '<span style="color:green">';
    } else if (value == "skip") {
	ret = '<span style="color:blue">';
    } else {
	ret = '<span style="color:red">';
    }
    return ret + value + '</span>';
}

function pw_filter_r(v, r, drop_reported)
{
    if (loaded_filters == null)
	return false;

    var reported_exec = false;
    for (const exec of loaded_filters.executors) {
	if (v.executor == exec) {
	    reported_exec = true;
	    break;
	}
    }

    if (reported_exec == false && drop_reported == true)
	return false;

    var reported_test = true;
    for (const test of loaded_filters["ignore-tests"]) {
	if (r.group == test.group && r.test == test.test) {
	    reported_test = false;
	    break;
	}
    }
    if ((reported_test && reported_exec) == drop_reported)
	return true;

    return false;
}

function load_result_table(data_raw)
{
    var table = document.getElementById("results");
    var result_filter = {
	"pass": document.getElementById("pass").checked,
	"skip": document.getElementById("skip").checked,
	"warn": document.getElementById("warn").checked,
	"fail": document.getElementById("fail").checked
    };
    var branch_filter = document.getElementById("branch").value;
    var exec_filter = document.getElementById("executor").value;
    var test_filter = document.getElementById("test-name").value;
    var pw_n = document.getElementById("pw-n").checked;
    var pw_y = document.getElementById("pw-y").checked;

    // Remove all rows but first (leave headers)
    $("#results tr").slice(1).remove();

    let warn_box = document.getElementById("fl-warn-box");
    if (!exec_filter && !test_filter && !branch_filter) {
	warn_box.innerHTML = "Set an executor, branch or test filter. Otherwise this page will set your browser on fire...";
	return;
    } else {
	warn_box.innerHTML = "";
    }

    $.each(data_raw, function(i, v) {
	if (branch_filter &&
	    branch_filter != v.branch)
	    return 1;
	if (exec_filter &&
	    exec_filter != v.executor)
	    return 1;

	$.each(v.results, function(j, r) {
	    if (test_filter &&
		r.test != test_filter)
		return 1;
	    if (result_filter[r.result] == false)
		return 1;
	    if (pw_y == false && pw_filter_r(v, r, true))
		return 1;
	    if (pw_n == false && pw_filter_r(v, r, false))
		return 1;

	    var row = table.insertRow();

	    var date = row.insertCell(0);
	    var branch = row.insertCell(1);
	    var remote = row.insertCell(2);
	    var exe = row.insertCell(3);
	    var group = row.insertCell(4);
	    var test = row.insertCell(5);
	    var res = row.insertCell(6);

	    date.innerHTML = v.end.toLocaleString();
	    branch.innerHTML = v.branch;
	    remote.innerHTML = v.remote;
	    exe.innerHTML = v.executor;
	    group.innerHTML = r.group;
	    test.innerHTML = "<a href=\"" + r.link + "\">" + r.test + "</a>";
	    res.innerHTML = colorify_str(r.result);
	});
    });
}

function add_option_filter(data_raw, elem_id, field)
{
    var elem = document.getElementById(elem_id);
    var values = new Set();

    $.each(data_raw, function(i, v) {
	values.add(v[field]);
    });
    for (const value of values) {
	const opt = document.createElement('option');
	opt.value = value;
	opt.innerHTML = value;
	elem.appendChild(opt);
    }
    elem.addEventListener("change", results_update);
    elem.disabled = false;
}

function set_search_from_url()
{
    const urlParams = new URLSearchParams(window.location.search);
    const results = ["pass", "skip", "warn", "fail", "pw-y", "pw-n"];

    for (const r of results) {
	const elem = document.getElementById(r);

	if (urlParams.get(r) == "0")
	    elem.checked = false;
    }

    const br = document.getElementById("branch");
    if (urlParams.get("branch"))
	br.value = urlParams.get("branch");

    const ex = document.getElementById("executor");
    if (urlParams.get("executor"))
	ex.value = urlParams.get("executor");

    const test = document.getElementById("test-name");
    if (urlParams.get("test"))
	test.value = urlParams.get("test");
}

function results_update()
{
    load_result_table(loaded_data);
}

let xfr_todo = 2;
let loaded_data = null;
let loaded_filters = null;

function loaded_one()
{
    if (--xfr_todo)
	return;

    // We have all JSONs now, do processing.
    let warn_box = document.getElementById("fl-warn-box");
    warn_box.innerHTML = "";

    add_option_filter(loaded_data, "branch", "branch");
    add_option_filter(loaded_data, "executor", "executor");

    set_search_from_url();

    const fl_state = document.querySelectorAll("input[name=fl-state]");
    for (const one of fl_state) {
	one.addEventListener("change", results_update);
	one.disabled = false;
    }

    const fl_pw = document.querySelectorAll("input[name=fl-pw]");
    for (const one of fl_pw) {
	one.addEventListener("change", results_update);
	one.disabled = false;
    }

    results_update();
}

function filters_loaded(data_raw)
{
    loaded_filters = data_raw;
    loaded_one();
}

function results_loaded(data_raw)
{
    $.each(data_raw, function(i, v) {
	v.start = new Date(v.start);
	v.end = new Date(v.end);
    });
    data_raw.sort(function(a, b){return b.end - a.end;});

    loaded_data = data_raw;
    loaded_one();
}

function do_it()
{
    $(document).ready(function() {
        $.get("contest/filters.json", filters_loaded)
    });

    $(document).ready(function() {
        $.get("contest/all-results.json", results_loaded)
    });
}
