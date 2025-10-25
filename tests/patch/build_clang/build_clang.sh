#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

cc="ccache clang"
output_dir=build_clang/
ncpu=$(grep -c processor /proc/cpuinfo)
build_flags="-Oline -j $ncpu W=1"
tmpfile_o=$(mktemp)
tmpfile_n=$(mktemp)
rc=0

prep_config() {
  make LLVM=1 O=$output_dir allmodconfig
  ./scripts/config --file $output_dir/.config -d werror
  ./scripts/config --file $output_dir/.config -d drm_werror
  ./scripts/config --file $output_dir/.config -d kvm_werror
}

echo "Using $build_flags redirect to $tmpfile_o and $tmpfile_n"
echo "LLVM=1 cc=\"$cc\""
$cc --version | head -n1

HEAD=$(git rev-parse HEAD)

echo "Tree base:"
git log -1 --pretty='%h ("%s")' HEAD~

if [ x$FIRST_IN_SERIES == x0 ] && \
   ! git diff --name-only HEAD~ | grep -q -E "Kconfig$"
then
    echo "Skip baseline build, not the first patch and no Kconfig updates"
else
    echo "Baseline building the tree"

    prep_config
    make LLVM=1 O=$output_dir CC="$cc" $build_flags
fi

# Check if new files were added, new files will cause mod re-linking
# so all module and linker related warnings will pop up in the "after"
# but not "before". To avoid this we need to force re-linking on
# the "before", too.
touch_relink=/dev/null
if ! git log --diff-filter=A HEAD~.. --exit-code >>/dev/null || \
   git diff --name-only HEAD~ | grep -q -E "Makefile$" || \
   git diff --name-only HEAD~ | grep -q -E "Kconfig$"
then
    echo "Trying to force re-linking, new files were added"
    touch_relink=${output_dir}/include/generated/utsrelease.h
fi

touch $touch_relink

git checkout -q HEAD~

echo "Building the tree before the patch"

prep_config
make LLVM=1 O=$output_dir CC="$cc" $build_flags 2> >(tee $tmpfile_o >&2)
incumbent=$(grep -i -c "\(warn\|error\)" $tmpfile_o)

echo "Building the tree with the patch"

git checkout -q $HEAD

# Also force rebuild "after" in case the file added isn't important.
touch $touch_relink

prep_config
make LLVM=1 O=$output_dir CC="$cc" $build_flags 2> >(tee $tmpfile_n >&2) || rc=1

current=$(grep -i -c "\(warn\|error\)" $tmpfile_n)

echo "Errors and warnings before: $incumbent this patch: $current" >&$DESC_FD

if [ $current -gt $incumbent ]; then
  echo "New errors added" 1>&2
  diff -U 0 $tmpfile_o $tmpfile_n 1>&2

  echo "Per-file breakdown" 1>&2
  tmpfile_fo=$(mktemp)
  tmpfile_fn=$(mktemp)

  grep -i "\(warn\|error\)" $tmpfile_o | sed -n 's@\(^\.\./[/a-zA-Z0-9_.-]*.[ch]\):.*@\1@p' | sort | uniq -c \
    > $tmpfile_fo
  grep -i "\(warn\|error\)" $tmpfile_n | sed -n 's@\(^\.\./[/a-zA-Z0-9_.-]*.[ch]\):.*@\1@p' | sort | uniq -c \
    > $tmpfile_fn

  diff -U 0 $tmpfile_fo $tmpfile_fn 1>&2
  rm $tmpfile_fo $tmpfile_fn

  rc=1
fi

rm $tmpfile_o $tmpfile_n

exit $rc
