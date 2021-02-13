#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.
# Copyright (c) 2020 Facebook

tmpfile_o=$(mktemp)
tmpfile_n=$(mktemp)
rc=0

files=$(git show --pretty="" --name-only HEAD)

HEAD=$(git rev-parse HEAD)

echo "Checking the tree before the patch"
git checkout -q HEAD~
./scripts/kernel-doc -none $files 2> >(tee $tmpfile_o >&2)

incumbent=$(grep -v 'Error: Cannot open file ' $tmpfile_o | wc -l)

echo "Checking the tree with the patch"

git checkout -q $HEAD
./scripts/kernel-doc -none $files 2> >(tee $tmpfile_n >&2)

current=$(grep -v 'Error: Cannot open file ' $tmpfile_n | wc -l)

echo "Errors and warnings before: $incumbent this patch: $current" >&$DESC_FD

if [ $current -gt $incumbent ]; then
  echo "New warnings added" 1>&2
  diff $tmpfile_o $tmpfile_n 1>&2

  echo "Per-file breakdown" 1>&2
  tmpfile_fo=$(mktemp)
  tmpfile_fn=$(mktemp)

  grep -i "\(warn\|error\)" $tmpfile_o | sed -n 's@\(^\.\./[/a-zA-Z0-9_.-]*.[ch]\):.*@\1@p' | sort | uniq -c \
    > $tmpfile_fo
  grep -i "\(warn\|error\)" $tmpfile_n | sed -n 's@\(^\.\./[/a-zA-Z0-9_.-]*.[ch]\):.*@\1@p' | sort | uniq -c \
    > $tmpfile_fn

  diff $tmpfile_fo $tmpfile_fn 1>&2
  rm $tmpfile_fo $tmpfile_fn

  rc=1
fi

rm $tmpfile_o $tmpfile_n

exit $rc
