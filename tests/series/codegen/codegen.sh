#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2023 Meta Platforms, Inc. and affiliates

tmpfile=$(mktemp)
rc=0

./tools/net/ynl/ynl-regen.sh -f

if git diff --quiet; then
  echo "Generated files up to date;" >&$DESC_FD
else
  echo "Tree is dirty after regen;" >&$DESC_FD
  git status --porcelain
  rc=1
fi

make -C tools/net/ynl/ hardclean
if ! make -C tools/net/ynl/ -j 16 2> >(tee $tmpfile >&2); then
  echo "build failed;" >&$DESC_FD
  rc=1
fi

cnt=$(grep -i -c "\(warn\|error\)" $tmpfile)

if [ $cnt -eq 0 ]; then
  echo "no warnings/errors;" >&$DESC_FD
else
  echo "build has $cnt warnings/errors;" >&$DESC_FD
  rc=1
fi

rm $tmpfile

exit $rc
