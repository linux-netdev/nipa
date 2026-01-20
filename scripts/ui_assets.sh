#!/bin/bash
#
# This script manages all the assets a UI running locally
# on your computer would need.
#
# Note we are downloading real assets from a production instance.
#
# Usage examples:
#
#     ./scripts/ui_assets.sh download
#     ./scripts/ui_assets.sh clean

set -eu

PROD=https://netdev.bots.linux.dev
LOCAL=./ui
ASSETS=(
  "checks.json"
  "status.json"
  "contest/branch-results.json"
  "contest/branches-info.json"
  "contest/filters.json"
  "contest/all-results.json"
)

function usage() {
  echo "Usage: ${0} download|clean"
}

function download() {
  mkdir -p "${LOCAL}/static/nipa"
  mkdir -p "${LOCAL}/contest"
  for asset in "${ASSETS[@]}"; do
    curl "${PROD}/${asset}" -o "${LOCAL}/${asset}"
  done
}

function clean() {
  for asset in "${ASSETS[@]}"; do
    rm -f "${LOCAL}/${asset}"
  done
  rm -r "${LOCAL}/static"
  rm -r "${LOCAL}/contest"
}

# Change dir to project root
cd "$(git rev-parse --show-toplevel)"

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

case $1 in
  download)
    download
    ;;
  clean)
    clean
    ;;
  *)
    echo >&2 "Error: Unrecognized subcommand $1"
    usage
    exit 1
    ;;
esac
