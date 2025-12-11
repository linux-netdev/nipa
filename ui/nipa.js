function nipa_msec_to_str(msec) {
    const convs = [
        [1, "ms"],
        [1000, "s"],
        [60, "m"],
        [60, "h"],
        [24, "d"],
        [7, "w"]
    ];

    if (msec <= 0)
	return msec.toString();

    for (i = 0; i < convs.length; i++) {
        if (msec < convs[i][0]) {
            var full = Math.floor(msec) + convs[i - 1][1];
            if (i > 1) {
                var frac = Math.round(msec * convs[i - 1][0] % convs[i - 1][0]);
                if (frac)
                    full += " " + frac + convs[i - 2][1];
            }
            return full;
        }
        msec /= convs[i][0];
    }

    return "TLE";
}

function nipa_br_pfx_get(name)
{
    return name.substring(0, name.length - 18);
}

function nipa_test_fullname(v, r)
{
    return v.remote + "/" + v.executor + "/" + r.group + "/" + r.test;
}

function __nipa_filters_set(update_cb, set_name, enabled)
{
    if (set_name.constructor === Array) {
	for (name of set_name)
	    __nipa_filters_set(update_cb, name, enabled);
	return;
    }

    const fl_pw = document.querySelectorAll("[name=" + set_name + "]");
    for (const one of fl_pw) {
	if (update_cb)
	    one.addEventListener("change", update_cb);
	one.disabled = enabled;
    }
}

function nipa_filters_enable(update_cb, set_name)
{
    let warn_box = document.getElementById("fl-warn-box");
    warn_box.innerHTML = "";

    __nipa_filters_set(update_cb, set_name, false);
}

function nipa_filters_disable(set_name)
{
    let warn_box = document.getElementById("fl-warn-box");
    warn_box.innerHTML = "Loading...";

    __nipa_filters_set(null, set_name, true);
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

function nipa_select_add_option(select_elem, show_str, value)
{
    const opt = document.createElement('option');
    opt.value = value;
    opt.innerHTML = show_str;
    select_elem.appendChild(opt);
}

function nipa_filter_add_options(data_raw, elem_id, field)
{
    var elem = document.getElementById(elem_id);
    var values = new Set();

    // Re-create "all"
    nipa_select_add_option(elem, "-- all --", "");

    // Create the dynamic entries
    $.each(data_raw, function(i, v) {
	if (field)
	    values.add(v[field]);
	else
	    values.add(v);
    });
    for (const value of values) {
	nipa_select_add_option(elem, value, value);
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
	$("#sitemap").load("/sitemap.html")
    });
}

function nipa_load_sponsors()
{
    $(document).ready(function() {
	$("body").append('<div id="sponsors"></div>');
	$("#sponsors").load("/sponsors.html");
    });
}

// ------------------

var nipa_sort_cb = null;
let nipa_sort_keys = [];
let nipa_sort_polarity = [];

function nipa_sort_key_set(event)
{
    let elem = event.target;
    let what = elem.innerText.toLowerCase().replace(/[^a-z0-9]/g, '');
    const index = nipa_sort_keys.indexOf(what);
    let polarity = 1;

    if (index != -1) {
	polarity = nipa_sort_polarity[index];

	// if it's the main sort key invert direction, otherwise we're changing
	// order of keys but not their direction
	let main_key = index == nipa_sort_keys.length - 1;
	if (main_key)
	    polarity *= -1;

	// delete it
	nipa_sort_keys.splice(index, 1);
	nipa_sort_polarity.splice(index, 1);
	elem.innerText = elem.innerText.slice(0, -2);

	// We flipped back to normal polarity, that's a reset
	if (main_key && polarity == 1) {
	    elem.classList.remove('column-sorted');
	    nipa_sort_cb();
	    return;
	}
    } else {
	elem.classList.add('column-sorted');
    }

    if (polarity == 1) {
	elem.innerHTML = elem.innerText + " &#11206;";
    } else {
	elem.innerHTML = elem.innerText + " &#11205;";
    }

    // add it
    nipa_sort_keys.push(what);
    nipa_sort_polarity.push(polarity);

    nipa_sort_cb();
}

function nipa_sort_get(what)
{
    const index = nipa_sort_keys.indexOf(what);

    if (index == -1)
	return 0;
    return nipa_sort_polarity[index];
}
