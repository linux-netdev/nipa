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
