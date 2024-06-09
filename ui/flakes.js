function colorify(cell, value)
{
    if (value == "pass" || value == "skip" ||
	value == "fail" || value == "flake")
	cell.setAttribute("class", "box-" + value);
}

function get_sort_key()
{
    if (document.getElementById("sort-streak").checked)
	return "streak";
    return "cnt";
}

function load_result_table(data_raw)
{
    // Get all branch names
    var branch_set = new Set();
    $.each(data_raw, function(i, v) {
	branch_set.add(v.branch);
    });
    let br_cnt = document.getElementById("br-cnt").value;
    const branches = Array.from(branch_set).slice(0, br_cnt);

    // Build the result map
    var pw_n = document.getElementById("pw-n").checked;
    var pw_y = document.getElementById("pw-y").checked;
    let needle = document.getElementById("tn-needle").value;

    var test_row = {};
    let tn_urls = {};

    $.each(data_raw, function(i, v) {
	$.each(v.results, function(j, r) {
	    if (pw_y == false && nipa_pw_reported(v, r) == true)
		return 1;
	    if (pw_n == false && nipa_pw_reported(v, r) == false)
		return 1;

	    const tn = v.remote + '/' + r.group + '/' + r.test;
	    if (needle && !tn.includes(needle))
		return 1;

	    tn_urls[tn] = "executor=" + v.executor + "&test=" + r.test;

	    if (!(tn in test_row)) {
		test_row[tn] = {};
		for (let i = 1; i <= branches.length; i++)
		    test_row[tn][branches[i - 1]] = "";
	    }
	    test_row[tn][v.branch] = r.result;
	    if (r.result == "fail" && r.retry == "pass")
		test_row[tn][v.branch] = "flake";
	});
    });

    // Sort from most to least flaky
    for (const [tn, entries] of Object.entries(test_row)) {
	let count = 0, streak = 0, total = 0;
	let prev = "pass";

	for (let i = 0; i < branches.length; i++) {
	    let current = entries[branches[i]];

	    if (current != "")
		total++;

	    if (current == "pass" && count == 0)
		streak++;

	    if (current != "" && current != prev) {
		prev = current;
		count++;
	    }
	}
	test_row[tn]["total"] = total;
	test_row[tn]["cnt"] = count;
	test_row[tn]["streak"] = streak;
    }

    // Filter out those not flaky enough to show
    var min_flip = document.getElementById("min-flip").value;
    let test_names = Array.from(Object.keys(test_row));
    test_names = test_names.filter(function(a){return test_row[a].cnt >= min_flip;});
    // Sort by the right key
    var sort_key = get_sort_key();
    test_names.sort(
	function(a, b) { return test_row[b][sort_key] - test_row[a][sort_key]; }
    );

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

    let form = "";
    if (document.getElementById("ld-cases").checked)
	form = "&ld-cases=1";
    for (const tn of test_names) {
	let entries = test_row[tn];

	if (entries.total == 0)
	    continue;

	let row = table.insertRow();
	let name = row.insertCell(0);
	name.innerHTML = "<a style=\"text-decoration: none\" href=\"contest.html?" + tn_urls[tn] + form + "\">" + tn + "</a>";
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

let xfr_todo = 3;
let loaded_data = null;

function loaded_one()
{
    if (--xfr_todo)
	return;

    // We have all JSONs now, do processing.
    nipa_input_set_from_url("fl-pw");
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

    const had_data = loaded_data;
    loaded_data = data_raw;
    if (!had_data) {
	loaded_one();
    } else if (!xfr_todo) {
	results_update();
    }

    nipa_filters_enable(null, ["ld-pw", "fl-pw"]);
}

function remotes_loaded(data_raw)
{
    nipa_filter_add_options(data_raw, "ld-remote", null);
    loaded_one();
}

function reload_data()
{
    const format_l2 = document.getElementById("ld-cases");
    const br_cnt = document.getElementById("br-cnt");
    const remote = document.getElementById("ld-remote");

    let req_url = "query/results";
    req_url += "?branches=" + br_cnt.value;

    if (format_l2.checked)
	req_url += "&format=l2";
    if (remote.value)
	req_url += "&remote=" + remote.value;

    nipa_filters_disable(["ld-pw", "fl-pw"]);
    $(document).ready(function() {
        $.get(req_url, results_loaded)
    });
}

function do_it()
{
    nipa_filters_enable(reload_data, "ld-pw");
    nipa_filters_enable(results_update, "fl-pw");
    nipa_input_set_from_url("ld-pw");

    /*
     * Please remember to keep these assets in sync with `scripts/ui_assets.sh`
     */
    $(document).ready(function() {
        $.get("contest/filters.json", filters_loaded)
    });
    $(document).ready(function() {
        $.get("query/remotes", remotes_loaded)
    });
    reload_data();
}
