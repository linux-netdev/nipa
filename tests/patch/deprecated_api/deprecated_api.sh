#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

errors=( module_param )
warnings=( "\Wdev_hold(" "\Wdev_put(" )

res=0
msg=""

check_item() {
  total=$(git show | grep -i '^\+.*'"$item")

  if [ -n "$total" ]; then
    new=$(git show | grep -ic '^\+.*'"$item")
    old=$(git show | grep -ic '^\-.*'"$item")

    res=$1

    if [ "$new" -ne "$old" ]; then
      res=$1
    elif [ "$1" -eq 1 ]; then
      res=250
    else
      return
    fi

    item_name="'${item//\\W/}'"
    msg="$msg; $item_name was: $old now: $new"
    export res
    export msg
  fi
}

for item in "${warnings[@]}"; do
  check_item 250
done

for item in "${errors[@]}"; do
  check_item 1
done

if [ -z "$msg" ]; then
  msg="None detected"
else
  # Skip the starting "; "
  msg="Found: ${msg:2}"
fi

echo -e "$msg" >&$DESC_FD
exit $res
