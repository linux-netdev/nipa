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

function nipa_filters_set_from_url()
{
    const urlParams = new URLSearchParams(window.location.search);
    const filters = document.querySelectorAll("[name=fl-pw]");

    for (const elem of filters) {
	let url_val = urlParams.get(elem.id);

	if (!url_val)
	    continue;

	if (elem.hasAttribute("checked")) {
	    if (url_val == "0")
		elem.checked = false;
	    else if (url_val == "1")
		elem.checked = true;
	} else {
	    elem.value = url_val;
	}
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
