#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

inlines=$(git show -- '*.c' | grep -i '^\+\(.*\W\|\)inline\W')

if [ -z "$inlines" ]; then
  exit 0
else
  echo -e "Detected inline keyword in C files\n$inlines" 1>&2
  exit 1
fi
