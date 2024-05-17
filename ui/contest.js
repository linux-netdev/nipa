function colorify_str(value)
{
    if (value == "pass") {
	ret = '<span style="color:green">';
    } else if (value == "skip") {
	ret = '<span style="color:#809fff">';
    } else {
	ret = '<span style="color:red">';
    }
    return ret + value + '</span>';
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
    var remote_filter = document.getElementById("remote").value;
    var test_filter = document.getElementById("test").value;
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
	if (remote_filter &&
	    remote_filter != v.remote)
	    return 1;

	$.each(v.results, function(j, r) {
	    if (test_filter &&
		r.test != test_filter)
		return 1;
	    if (result_filter[r.result] == false)
		return 1;
	    if (pw_y == false && nipa_pw_reported(v, r) == true)
		return 1;
	    if (pw_n == false && nipa_pw_reported(v, r) == false)
		return 1;

	    var row = table.insertRow();

	    var date = row.insertCell(0);
	    var branch = row.insertCell(1);
	    var remote = row.insertCell(2);
	    var exe = row.insertCell(3);
	    var group = row.insertCell(4);
	    var test = row.insertCell(5);
	    var res = row.insertCell(6);
	    let row_id = 7;
	    var retry = row.insertCell(row_id++);
	    var outputs = row.insertCell(row_id++);
	    var flake = row.insertCell(row_id++);
	    var hist = row.insertCell(row_id++);

	    date.innerHTML = v.end.toLocaleString();
	    branch.innerHTML = "<a href=\"" + branch_urls[v.branch] + "\">" + v.branch + "</a>";
	    remote.innerHTML = v.remote;
	    exe.innerHTML = v.executor;
	    group.innerHTML = r.group;
	    test.innerHTML = "<b>" + r.test + "</b>";
	    if ("retry" in r)
		retry.innerHTML = colorify_str(r.retry);
	    res.innerHTML = colorify_str(r.result);
	    outputs.innerHTML = "<a href=\"" + r.link + "\">outputs</a>";
	    hist.innerHTML = "<a href=\"contest.html?test=" + r.test + "\">history</a>";
	    flake.innerHTML = "<a href=\"flakes.html?tn-needle=" + r.test + "\">matrix</a>";
	});
    });
}

function find_branch_urls(loaded_data)
{
    $.each(loaded_data, function(i, v) {
	if (v.remote == "brancher")
	    branch_urls[v.branch] = v.results[0].link;
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
}

function results_update()
{
    load_result_table(loaded_data);
}

let xfr_todo = 2;
let branch_urls = {};
let loaded_data = null;

function loaded_one()
{
    if (--xfr_todo)
	return;

    // We have all JSONs now, do processing.
    add_option_filter(loaded_data, "branch", "branch");
    add_option_filter(loaded_data, "executor", "executor");
    add_option_filter(loaded_data, "remote", "remote");

    nipa_filters_set_from_url();
    nipa_filters_enable(results_update);

    results_update();
}

function filters_loaded(data_raw)
{
    nipa_set_filters_json(data_raw);
    loaded_one();
}

function results_loaded(data_raw)
{
    $.each(data_raw, function(i, v) {
	v.start = new Date(v.start);
	v.end = new Date(v.end);
    });
    data_raw.sort(function(a, b){return b.end - a.end;});

    find_branch_urls(data_raw);

    loaded_data = data_raw;
    loaded_one();
}

function do_it()
{
    /*
     * Please remember to keep these assets in sync with `scripts/ui_assets.sh`
     */
    $(document).ready(function() {
        $.get("contest/filters.json", filters_loaded)
    });
    $(document).ready(function() {
        $.get("query/results?branches=100", results_loaded)
    });
}
