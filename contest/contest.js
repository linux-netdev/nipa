function colorify_str(value)
{
    if (value == "pass") {
	ret = '<p style="color:green">';
    } else if (value == "skip") {
	ret = '<p style="color:blue">';
    } else {
	ret = '<p style="color:red">';
    }
    return ret + value + '</p>';
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

    // Remove all rows but first (leave headers)
    $("#results tr").slice(1).remove();
    $.each(data_raw, function(i, v) {
	$.each(v.results, function(j, r) {
	    if (result_filter[r.result] == false)
		return 1;
	    if (branch_filter &&
		branch_filter != v.branch)
		return 1;
	    if (exec_filter &&
		exec_filter != v.executor)
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

let loaded_data = null;

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
}

function results_doit(data_raw)
{
    const ingredients = document.querySelectorAll("input[name=fl-state]");

    for (const ingredient of ingredients) {
	ingredient.addEventListener("change", results_update);
    }

    $.each(data_raw, function(i, v) {
	v.start = new Date(v.start);
	v.end = new Date(v.end);
    });

    data_raw.sort(function(a, b){return b.end - a.end;});

    add_option_filter(data_raw, "branch", "branch");
    add_option_filter(data_raw, "executor", "executor");

    loaded_data = data_raw;
    load_result_table(data_raw);
}

function results_update()
{
    if (loaded_data != 1) {
	load_result_table(loaded_data);
    }
}

function do_it()
{
    if (loaded_data == null) {
	loaded_data = 1;

	$(document).ready(function() {
            $.get("contest/all-results.json", results_doit)
	});
    } else if (loaded_data == 1) {
	/* nothing, loading in progress */
    } else {
	load_result_table(loaded_data);
    }
}
