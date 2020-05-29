#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

cc="ccache gcc"
output_dir=build_allmodconfig_warn/
ncpu=$(grep -c processor /proc/cpuinfo)
build_flags="-j $ncpu W=1 C=1"
tmpfile_o=$(mktemp)
tmpfile_n=$(mktemp)
rc=0

echo "Using -j $ncpu redirect to $tmpfile_o and $tmpfile_n"

HEAD=$(git rev-parse HEAD)

git checkout -q HEAD~

echo "Building the tree before the patch"

make CC="$cc" O=$output_dir allmodconfig
make CC="$cc" O=$output_dir $build_flags 2> >(tee $tmpfile_o >&2)
incumbent=$(grep -i -c "\(warn\|error\)" $tmpfile_o)

echo "Building the tree with the patch"

git checkout -q $HEAD

make CC="$cc" O=$output_dir allmodconfig
make CC="$cc" O=$output_dir $build_flags -j $ncpu 2> >(tee $tmpfile_n >&2) || rc=1

current=$(grep -i -c "\(warn\|error\)" $tmpfile_n)

if [ $current -gt $incumbent ]; then
  echo "Errors and warnings before: $incumbent this patch: $current" >&$DESC_FD
  echo "New errors added" 1>&2
  diff $tmpfile_o $tmpfile_n 1>&2
  rc=1
fi

rm $tmpfile_o $tmpfile_n

exit $rc
