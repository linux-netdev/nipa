function colorify(cell, value)
{
    if (value == "") {
	ret = "";
    } else if (value == "pass") {
	ret = "background-color:green";
    } else if (value == "skip") {
	ret = "background-color:blue";
    } else {
	ret = "background-color:red";
    }
    cell.setAttribute("style", ret);
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
    // Get all branch names
    var branch_set = new Set();
    $.each(data_raw, function(i, v) {
	branch_set.add(v.branch);
    });
    const branches = Array.from(branch_set);

    // Build the result map
    var pw_n = document.getElementById("pw-n").checked;
    var pw_y = document.getElementById("pw-y").checked;

    var test_row = {};

    $.each(data_raw, function(i, v) {
	$.each(v.results, function(j, r) {
	    if (pw_y == false && pw_filter_r(v, r, true))
		return 1;
	    if (pw_n == false && pw_filter_r(v, r, false))
		return 1;

	    const tn = v.remote + '/' + r.group + '/' + r.test;

	    if (!(tn in test_row)) {
		test_row[tn] = {};
		for (let i = 1; i <= branches.length; i++)
		    test_row[tn][branches[i - 1]] = "";
	    }
	    test_row[tn][v.branch] = r.result;
	});
    });

    // Sort from most to least flaky
    for (const [tn, entries] of Object.entries(test_row)) {
	let count = 0;
	let prev = "pass";

	for (let i = 0; i < branches.length; i++) {
	    let current = entries[branches[i]];
	    if (current != "" && current != prev) {
		prev = current;
		count++;
	    }
	}
	test_row[tn]["cnt"] = count;
    }

    // Filter out those not flaky enough to show
    var min_flip = document.getElementById("min-flip").value;
    let test_names = Array.from(Object.keys(test_row));
    test_names = test_names.filter(function(a){return test_row[a].cnt >= min_flip;});
    test_names.sort(function(a, b){return test_row[b].cnt - test_row[a].cnt;});

    // Remove all rows but first (leave headers)
    $("#results tr").remove();
    // Display
    let table = document.getElementById("results");

    let header = table.insertRow();
    header.insertCell(0); // name
    for (let i = 0; i < branches.length; i++) {
	let cell = header.insertCell(i + 1);
	cell.innerHTML = branches[i];
	cell.setAttribute("style", "writing-mode: tb-rl; font-size: 0.8em; padding: 0px;");
    }

    for (const tn of test_names) {
	let entries = test_row[tn];
	let row = table.insertRow();

	let name = row.insertCell(0);
	name.innerHTML = tn;
	name.setAttribute("style", "padding: 0px");

	for (let i = 0; i < branches.length; i++) {
	    let cell = row.insertCell(i + 1);
	    colorify(cell, entries[branches[i]]);
	}
    }
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
    const ingredients = document.querySelectorAll("input[name=fl-pw]");

    for (const ingredient of ingredients) {
	ingredient.addEventListener("change", results_update);
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
