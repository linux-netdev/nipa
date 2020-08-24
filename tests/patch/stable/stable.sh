#!/bin/bash
# SPDX-License-Identifier: GPL-2.0

stable=$(git show -s | grep -i '^ *cc:.*stable@kernel.org')

if [ -z "$stable" ]; then
  echo "Stable not CCed" >&$DESC_FD
  exit 0
else
  echo "Stable CC detected: $stable" >&$DESC_FD
  echo -e "Detected stable CC\n$stable" 1>&2
  exit 1
fi
