#!/bin/bash
#
# This script opens your local checkout of the UI in your browser.
#
# Note you need to have run `scripts/ui_assets.sh download` at least
# once prior to running this script.
#
# Usage example:
#
#     ./scripts/ui.sh

set -eu

# Change dir to project root
cd "$(git rev-parse --show-toplevel)"

# Quick sanity check
if [[ ! -d ./ui/static ]]; then
  echo >&2 "Error: you haven't run scripts/ui_assets.sh yet"
  exit 1
fi

# Need to run a local webserver to avoid CORS violations
python -m http.server -d ./ui -b localhost 8080 &> /dev/null &
pid=$!
trap 'kill ${pid}' EXIT

# Best effort in case someone is on OSX or SSH forwarding
if ! xdg-open http://localhost:8080/status.html &> /dev/null; then
  echo "UI is available at http://localhost:8080/status.html"
fi

echo "Press Enter to stop serving and exit..."
read -r -p ""
