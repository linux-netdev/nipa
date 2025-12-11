let xfr_todo = 2;
let dev_info = null;
let stability = null;

function load_tables()
{
    // Turn stability into matrix by executor
    let rn_seen = new Set();
    let tn_db = [];
    let sta_db = {};
    // Test age
    let tn_time = {};
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
    }

    // Simple sort by name
    tn_db.sort();

    // Render device info
    let display_names = {};
    let dev_table = document.getElementById("device_info");

    for (dev of dev_info) {
	let rn = dev.remote + dev.executor;
	if (!rn_seen.has(rn))
	    continue;

	let row = dev_table.insertRow();

	row.insertCell(0).innerText = dev.remote;
	row.insertCell(1).innerText = dev.executor;

	const info = JSON.parse(dev.info);
	const driver = info.driver;
	row.insertCell(2).innerText = driver;

	delete info.driver;
	const versions = JSON.stringify(info);
	row.insertCell(3).innerText = versions;

	display_names[dev.remote + dev.executor] =
	    dev.remote + '<br />' + dev.executor + '<br />' + driver;
    }

    // Create headers
    let sta_tb = document.getElementById("stability");
    let sta_to = document.getElementById("stability-old");

    for (tbl of [sta_tb, sta_to]) {
	const hdr = tbl.createTHead().insertRow();
	hdr.insertCell().innerText = 'Group';
	hdr.insertCell().innerText = 'Test';
	hdr.insertCell().innerText = 'Subtest';
	for (rn of Object.keys(display_names)) {
	    let cell = hdr.insertCell();

	    cell.innerHTML = display_names[rn];
	    cell.setAttribute("style", "writing-mode: tb-rl;");
	}
    }

    // Display
    let two_weeks_ago = new Date().setDate(new Date().getDate() - 14);

    for (tn of tn_db) {
	let row = null;

	if (tn_time[tn] > two_weeks_ago)
	    row = sta_tb.insertRow();
	else
	    row = sta_to.insertRow();

	row.insertCell(0).innerText = tn.split(':')[0];
	row.insertCell(1).innerText = tn.split(':')[1];
	let cell = row.insertCell(2);
	if (tn.split(':').length == 3)
	    cell.innerText = tn.split(':')[2];

	let i = 3;
	for (rn of Object.keys(display_names)) {
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

function do_it()
{
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
