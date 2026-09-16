Result metrics UI fixtures
==========================

Serve the ``ui`` directory::

  cd ui
  python3 -m http.server 8000

Then open::

  http://127.0.0.1:8000/metric-table.html?data=mock/result-metrics.json

The ``data`` parameter replaces the ``query/metrics`` backend, and the table
derives the matching ``result-metric-details.json`` path from it. Test-case
links open metric history; result links open complete observations. Filters
can be saved in the URL.

Without ``data``, the pages load ``query/metrics``, which
``scripts/ui_assets.sh download`` fetches from production for offline use.
Result details still need the backend.

``result-metrics.json`` models the compact ``/query/metrics`` response.
``result-metric-details.json`` adds the original timestamped observations.
The fixtures include devmem and iperf histories with regression examples.
