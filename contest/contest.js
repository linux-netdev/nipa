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

	$.each(data_raw, function(i, v) {
		v.start = new Date(v.start);
		v.end = new Date(v.end);
	});

    data_raw.sort(function(a, b){return b.end - a.end;});

    $.each(data_raw, function(i, v) {
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
	    group.innerHTML = v.group;
	    test.innerHTML = "<a href=\"" + v.link + "\">" + v.test + "</a>";
	    res.innerHTML = colorify_str(v.result);
    });
}

function results_doit(data_raw)
{
    load_result_table(data_raw);
}

function do_it()
{
    $(document).ready(function() {
        $.get("contest/all-results.json", results_doit)
    });
}
