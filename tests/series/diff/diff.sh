#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2026 Matthieu Baerts

if [ -z "${MSGID}" ]; then
  echo "MSGID not set" >&"${DESC_FD}"
  exit 0
fi

OUT="${RESULTS_DIR}/b4-diff.txt"

# ignore errors, we just want to see the diff if available
b4 diff --output-diff "${OUT}" "${MSGID}" || true

if [ -s "${OUT}" ]; then
  echo "Diff with the previous version in $(basename "${OUT}")" >&"${DESC_FD}"
else
  echo "No diff available" >&"${DESC_FD}"
fi
