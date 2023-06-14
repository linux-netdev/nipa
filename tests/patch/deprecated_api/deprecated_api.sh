#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2020 Facebook

errors=( module_param )
warnings=( "\Wdev_hold(" "\Wdev_put(" "\Wput_net(" "\Wget_net(" )

res=0
msg=""

check_item() {
  total=$(git show | grep -i '^+.*'"$item")

  if [ -n "$total" ]; then
    new=$(git show | grep -ic '^+.*'"$item")
    old=$(git show | grep -ic '^-.*'"$item")

    if [ $((new + old)) -eq 0 ]; then
      return
    fi

    item_name="'${item//\\W/}'"
    msg="$msg; $item_name was: $old now: $new"
    export msg

    if [ "$new" -eq 0 ]; then
      return
    elif [ "$new" -gt "$old" ]; then
      res=$1
      export res
    elif [ "$1" -eq 1 ]; then
      res=250
      export res
    fi
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
