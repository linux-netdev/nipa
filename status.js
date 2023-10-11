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

function do_it()
{
    $(document).ready(function() {
        $.get("static/nipa/checks.json", run_it)
    });
}
