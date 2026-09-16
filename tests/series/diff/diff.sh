#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2026 Matthieu Baerts

if [ -z "${MSGID}" ]; then
  echo "MSGID not set" >&"${DESC_FD}"
  exit 0
fi

no_diff() {
  echo "No diff available" >&"${DESC_FD}"
  exit 0
}

# ignore errors, we just want to see the diff if available
if ! B4_OUT=$(b4 diff -n "${MSGID}" 2>&1); then
  no_diff
fi

if ! GIT_RANGE=$(echo "${B4_OUT}" | tail -n1 |
                   awk '/^\s*git range-diff / { print $3" "$4 }'); then
  echo "Strange output from b4 diff, no git range found"
  echo "${B4_OUT}"
  no_diff
fi

HAS_DIFF=0
OUT="${RESULTS_DIR}/b4-diff.ansi"
DEFAULT=60
for factor in "${DEFAULT}" 40 80; do
  if [ "${factor}" -eq "${DEFAULT}" ]; then
    # default, we don't need to specify the factor
    out="${OUT}"
  else
    out="${RESULTS_DIR}/b4-diff-${factor}.ansi"
  fi

  # shellcheck disable=SC2086 # We want word splitting here
  if ! git range-diff --color=always --creation-factor="${factor}" \
           ${GIT_RANGE} > "${out}"; then
    echo "Error running git range-diff for factor ${factor}"
    cat "${out}" 2>/dev/null || true
    rm -f "${out}"
  elif ! grep -q "^    " "${out}" 2>/dev/null; then
    echo "Empty range-diff for factor ${factor}, removing ${out}"
    rm -f "${out}"
  else
    HAS_DIFF=1
    if [ "${factor}" -ne "${DEFAULT}" ] && cmp -s "${out}" "${OUT}"; then
      echo "Range-diff for factor ${factor} is identical to default, removing"
      rm -f "${out}"
    fi
  fi
done

if [ "${HAS_DIFF}" -eq 1 ]; then
  echo "Diff with the previous version available" >&"${DESC_FD}"
else
  no_diff
fi
