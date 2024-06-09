function nipa_test_fullname(v, r)
{
    return v.remote + "/" + v.executor + "/" + r.group + "/" + r.test;
}

function nipa_filters_enable(update_cb)
{
    let warn_box = document.getElementById("fl-warn-box");
    warn_box.innerHTML = "";

    const fl_pw = document.querySelectorAll("[name=fl-pw]");
    for (const one of fl_pw) {
	one.addEventListener("change", update_cb);
	one.disabled = false;
    }
}

function nipa_input_set_from_url(name)
{
    const urlParams = new URLSearchParams(window.location.search);
    const filters = document.querySelectorAll("[name="+ name + "]");

    for (const elem of filters) {
	let url_val = urlParams.get(elem.id);

	if (!url_val)
	    continue;

	if (elem.hasAttribute("checked") ||
	    elem.type == "radio" || elem.type == "checkbox") {
	    if (url_val == "0")
		elem.checked = false;
	    else if (url_val == "1")
		elem.checked = true;
	} else if (elem.type == "select-one") {
	    let option = elem.querySelector('[value="' + url_val + '"]');

	    if (!option) {
		const opt = document.createElement('option');
		opt.value = url_val;
		opt.innerHTML = url_val;
		opt.setAttribute("style", "display: none;");
		elem.appendChild(opt);
	    }
	    elem.value = url_val;
	} else {
	    elem.value = url_val;
	}
    }
}

function nipa_filters_set_from_url()
{
    nipa_input_set_from_url("fl-pw");
}

function nipa_filter_add_options(data_raw, elem_id, field)
{
    var elem = document.getElementById(elem_id);
    var values = new Set();

    // Re-create "all"
    const opt = document.createElement('option');
    opt.value = "";
    opt.innerHTML = "-- all --";
    elem.appendChild(opt);

    // Create the dynamic entries
    $.each(data_raw, function(i, v) {
	if (field)
	    values.add(v[field]);
	else
	    values.add(v);
    });
    for (const value of values) {
	const opt = document.createElement('option');
	opt.value = value;
	opt.innerHTML = value;
	elem.appendChild(opt);
    }
}

// ------------------

let nipa_filters_json = null;

function nipa_set_filters_json(filters_json)
{
    nipa_filters_json = filters_json;
}

// v == result info, r == particular result / test case
function nipa_pw_reported(v, r)
{
    for (const filter of nipa_filters_json["ignore-results"]) {
	if (!("remote" in filter) || filter.remote == v.remote) {
	    if (!("executor" in filter) || filter.executor == v.executor) {
		if (!("branch" in filter) || filter.branch == v.branch) {
		    if (!("group" in filter) || filter.group == r.group) {
			if (!("test" in filter) || filter.test == r.test) {
			    return false;
			}
		    }
		}
	    }
	}
    }

    return true;
}

function nipa_load_sitemap()
{
    $(document).ready(function() {
	$("#sitemap").load("sitemap.html")
    });
}
