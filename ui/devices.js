let xfr_todo = 2;
let dev_info = null;
let stability = null;

// Score a single result cell: 100% pass -> 100, 0% pass -> 0, anything
// else -> rate% - 100 (e.g. 80% -> -20). Flaky cases thus score lowest.
function cell_score(ste)
{
    let pct = Math.round(100 * ste.pass_cnt / (ste.fail_cnt + ste.pass_cnt));
    if (pct == 100)
	return 100;
    if (pct == 0)
	return 0;
    return pct - 100;
}

// Render one stability table: a header row of runner columns followed by
// a row per test case. When "sort by stability" is set the cases are
// ordered by their score summed over *this table's* columns only, so the
// ranking reflects exactly what is displayed.
function render_stability_table(table, cols, tns, sta_db, display_names)
{
    const hdr = table.createTHead().insertRow();
    hdr.insertCell().innerText = 'Group';
    hdr.insertCell().innerText = 'Test';
    hdr.insertCell().innerText = 'Subtest';
    for (rn of cols) {
	let cell = hdr.insertCell();

	cell.innerHTML = display_names[rn];
	cell.setAttribute("style", "writing-mode: tb-rl;");
    }

    if (document.getElementById("sort_by_stability").checked) {
	// Score each case over this table's columns, then sort most
	// problematic first, tie-break by name.
	let tn_score = {};
	for (tn of tns) {
	    let score = 0;
	    for (rn of cols)
		if (rn in sta_db[tn])
		    score += cell_score(sta_db[tn][rn]);
	    tn_score[tn] = score;
	}
	tns.sort(function(a, b) {
	    if (tn_score[a] != tn_score[b])
		return tn_score[a] - tn_score[b];
	    return a < b ? -1 : (a > b ? 1 : 0);
	});
    } else {
	tns.sort();
    }

    // Data rows go into a dedicated <tbody> so the <thead> can be sticky.
    let body = table.createTBody();
    for (tn of tns) {
	let row = body.insertRow();

	row.insertCell(0).innerText = tn.split(':')[0];
	row.insertCell(1).innerText = tn.split(':')[1];
	let cell = row.insertCell(2);
	if (tn.split(':').length == 3)
	    cell.innerText = tn.split(':')[2];

	let i = 3;
	for (rn of cols) {
	    cell = row.insertCell(i++);
	    if (rn in sta_db[tn]) {
		let ste = sta_db[tn][rn];

		pct = 100 * ste.pass_cnt / (ste.fail_cnt + ste.pass_cnt);
		pct = Math.round(pct);
		if (ste.passing) {
		    cell.setAttribute("class", "box-pass");
		    if (pct != 100)
			cell.innerText = pct + "%";
		} else {
		    cell.setAttribute("class", "box-skip");
		    if (pct != 0)
			cell.innerText = pct + "%";
		}
	    }
	}
    }
}

function load_tables()
{
    // Re-render from scratch (this may be called again when the
    // "show stale runners" checkbox is toggled).
    document.getElementById("device_info").innerHTML = "";
    document.getElementById("device_info-old").innerHTML = "";
    document.getElementById("stability").innerHTML = "";
    document.getElementById("stability-old").innerHTML = "";

    // Turn stability into matrix by executor
    let rn_seen = new Set();
    let tn_db = [];
    let sta_db = {};
    // Test age
    let tn_time = {};
    // Runner age (last report seen from each runner)
    let rn_time = {};
    // Overall stability score per runner (summed across all test cases)
    let rn_score = {};
    let year_ago = new Date();
    year_ago.setFullYear(year_ago.getFullYear() - 1);

    for (ste of stability) {
	let tn = ste.grp + ':' + ste.test + ':' + ste.subtest;
	if (ste.subtest == null)
	    tn = ste.grp + ':' + ste.test + ':';
	let rn = ste.remote + ste.executor;

	if (!(tn in sta_db)) {
	    sta_db[tn] = {};
	    tn_db.push(tn);
	    tn_time[tn] = year_ago;
	}

	sta_db[tn][rn] = ste;
	rn_seen.add(rn);
	let d = new Date(ste.last_update);
	if (d > tn_time[tn])
	    tn_time[tn] = d;
	if (!(rn in rn_time) || d > rn_time[rn])
	    rn_time[rn] = d;
	if (!(rn in rn_score))
	    rn_score[rn] = 0;
	rn_score[rn] += cell_score(ste);
    }

    let two_weeks_ago = new Date().setDate(new Date().getDate() - 14);

    // Render device info; runners idle for 2 weeks+ go to the "old" table.
    let display_names = {};
    let dev_table = document.getElementById("device_info");
    let dev_table_old = document.getElementById("device_info-old");

    for (tbl of [dev_table, dev_table_old]) {
	const hdr = tbl.createTHead().insertRow();
	hdr.insertCell().innerText = 'Remote';
	hdr.insertCell().innerText = 'Executor';
	hdr.insertCell().innerText = 'Driver';
	hdr.insertCell().innerText = 'Versions';
	hdr.insertCell().innerText = 'Score';
    }
    // Data rows go into a dedicated <tbody> (separate from the <thead>).
    let dev_body = dev_table.createTBody();
    let dev_body_old = dev_table_old.createTBody();

    for (dev of dev_info) {
	let rn = dev.remote + dev.executor;
	if (!rn_seen.has(rn))
	    continue;

	let row;
	if (rn_time[rn] > two_weeks_ago)
	    row = dev_body.insertRow();
	else
	    row = dev_body_old.insertRow();

	row.insertCell(0).innerText = dev.remote;
	row.insertCell(1).innerText = dev.executor;

	const info = JSON.parse(dev.info);
	const driver = info.driver;
	row.insertCell(2).innerText = driver;

	delete info.driver;
	const versions = JSON.stringify(info);
	row.insertCell(3).innerText = versions;

	row.insertCell(4).innerText = rn_score[rn];

	display_names[dev.remote + dev.executor] =
	    dev.remote + '<br />' + dev.executor + '<br />' + driver;
    }

    // Columns for the current table; unless the checkbox is ticked, hide
    // runners which have not reported anything in the last 2 weeks.
    let show_stale = document.getElementById("show_stale_runners").checked;
    let cols_cur = [];
    for (rn of Object.keys(display_names)) {
	if (show_stale || rn_time[rn] > two_weeks_ago)
	    cols_cur.push(rn);
    }
    // The "old" table keeps every runner (it is the archive).
    let cols_old = Object.keys(display_names);

    // Split test cases by recency, then render each table independently so
    // its case ordering reflects only the runners shown in that table.
    let sta_tb = document.getElementById("stability");
    let sta_to = document.getElementById("stability-old");

    let tns_cur = [];
    let tns_old = [];
    for (tn of tn_db) {
	if (tn_time[tn] > two_weeks_ago)
	    tns_cur.push(tn);
	else
	    tns_old.push(tn);
    }

    render_stability_table(sta_tb, cols_cur, tns_cur, sta_db, display_names);
    render_stability_table(sta_to, cols_old, tns_old, sta_db, display_names);
}

function do_it()
{
    document.getElementById("show_stale_runners")
	.addEventListener("change", load_tables);
    document.getElementById("sort_by_stability")
	.addEventListener("change", load_tables);

    $(document).ready(function() {
        $.get("query/device-info", function(data_raw) {
	    dev_info = data_raw;
	    if (!--xfr_todo)
		load_tables();
	})
    });
    $(document).ready(function() {
        $.get("query/stability?auto=1", function(data_raw) {
	    stability = data_raw;
	    if (!--xfr_todo)
		load_tables();
	})
    });
}
