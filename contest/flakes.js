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

let loaded_data = null;

function load_result_table(data_raw)
{
    var branch_set = new Set();
    $.each(data_raw, function(i, v) {
	branch_set.add(v.branch);
    });
    const branches = Array.from(branch_set);

    // Build the result map
    var test_row = {};

    $.each(data_raw, function(i, v) {
	$.each(v.results, function(j, r) {
	    const tn = r.group + '/' + r.test;

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

    let test_names = Array.from(Object.keys(test_row));
    test_names.sort(function(a, b){return test_row[b].cnt - test_row[a].cnt;});

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

function results_doit(data_raw)
{
    $.each(data_raw, function(i, v) {
	v.start = new Date(v.start);
	v.end = new Date(v.end);
    });

    data_raw.sort(function(a, b){return b.end - a.end;});

    loaded_data = data_raw;
    load_result_table(data_raw);
}

function do_it()
{
    $(document).ready(function() {
        $.get("contest/all-results.json", results_doit)
    });
}
