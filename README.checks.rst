======================
Checks stats/dashboard
======================

Checks dashboard generates a webpage with some stats about the checks
which are failing most often.

Fetcher
-------

``check_fetcher.py`` downloads the state of the checks from patchwork.
Note that this means the status is fetched from where ``pw_upload.py``
uploaded the results, not by consulting local file system.
Fetcher dumps the state of checks as a flat array of objects -
one object for each reported check (that is to say the state of
patches and checks is "flattened" together). Entry format:

.. code-block:: json

  {
    "id": 13372082,
    "date": "2023-09-01T06:21:27",
    "author": "Some Person",
    "state": "rfc",
    "delegate": "netdev",
    "check": "checkpatch",
    "result": "success",
    "description": "total: 0 errors, 0 warnings, 0 checks, 99 lines checked"
  },

Fetcher selects the checks by delegate so it will ignore all patches
set to other delegate (currently hardcoded to "netdev").

Fetcher has to be run periodically (e.g. from systemd timer), it does
one fetch and exists, it's not a daemon. It will write the results
into a file called ``checks.json`` in results directory.

Static site
-----------

``checks.html`` is the static HTML site, it contains the outline
which JavaScript then populates.

Renderer
--------

``checks.js`` is where most logic happens. It loads the JSON dumped
by fetcher with jQuery, analyzes it and loads the results into the page.
It uses Chart.js for the charts.
