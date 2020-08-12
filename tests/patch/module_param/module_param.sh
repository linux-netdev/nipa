#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

params=$(git show | grep -i '^\+.*module_param')
new_params=$(git show | grep -ic '^\+.*module_param')
old_params=$(git show | grep -ic '^\-.*module_param')

echo "Was $old_params now: $new_params" >&$DESC_FD

if [ -z "$params" ]; then
  exit 0
else
  echo -e "Detected module_param\n$params" 1>&2

  if [ $new_params -eq $old_params ]; then
    exit 250
  else
    exit 1
  fi
fi
