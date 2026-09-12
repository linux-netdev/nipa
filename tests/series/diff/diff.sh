#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2026 Matthieu Baerts

if [ -z "${MSGID}" ]; then
  echo "MSGID not set" >&"${DESC_FD}"
  exit 0
fi

OUT="${RESULTS_DIR}/b4-diff.ansi"

# ignore errors, we just want to see the diff if available
b4 diff --color --output-diff "${OUT}" "${MSGID}" || true

if grep -q "^    " "${OUT}" 2>/dev/null; then
  echo "Diff with the previous version in $(basename "${OUT}")" >&"${DESC_FD}"
else
  echo "No diff available" >&"${DESC_FD}"
fi
