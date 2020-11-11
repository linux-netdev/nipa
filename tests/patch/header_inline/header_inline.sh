#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

inlines=$(git show -- '*.h' | grep -C1 -P '^\+static (?!(__always_)?inline).*\(')

if [ -z "$inlines" ]; then
  exit 0
else
  echo -e "Detected static functions without inline keyword in C files\n$inlines" 1>&2
  exit 1
fi
