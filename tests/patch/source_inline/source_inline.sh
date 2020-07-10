#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

inlines=$(git show -- '*.c' | grep -i '^\+\([^*/]*\W\|\)inline\W')
new_inlines=$(git show -- '*.c' | grep -ic '^\+\([^*/]*\W\|\)inline\W')
old_inlines=$(git show -- '*.c' | grep -ic '^\-\([^*/]*\W\|\)inline\W')

echo "Was $old_inlines now: $new_inlines" >&$DESC_FD

if [ -z "$inlines" ]; then
  exit 0
else
  echo -e "Detected inline keyword in C files\n$inlines" 1>&2

  if [ $new_inlines -eq $old_inlines ]; then
    exit 250
  else
    exit 1
  fi
fi
