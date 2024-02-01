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
