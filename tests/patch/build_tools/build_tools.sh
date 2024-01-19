#!/bin/bash
# SPDX-License-Identifier: GPL-2.0

output_dir=build_tools/
ncpu=$(grep -c processor /proc/cpuinfo)
build_flags="-Oline -j $ncpu"
tmpfile_o=$(mktemp)
tmpfile_n=$(mktemp)
rc=0

# If it doesn't touch tools/ or include/, don't bother
if ! git diff --name-only HEAD~ | grep -E "^(include)|(tools)/"; then
    echo "No tools touched, skip" >&$DESC_FD
    exit 0
fi

# Looks like tools inherit WERROR, otherwise
make O=$output_dir allmodconfig
./scripts/config --file $output_dir/.config -d werror

echo "Using $build_flags redirect to $tmpfile_o and $tmpfile_n"
$cc --version | head -n1

HEAD=$(git rev-parse HEAD)

echo "Tree base:"
git log -1 --pretty='%h ("%s")' HEAD~

echo "Cleaning"
make O=$output_dir $build_flags -C tools/testing/selftests/ clean

echo "Building the tree before the patch"
git checkout -q HEAD~

make O=$output_dir $build_flags headers
for what in net net/forwarding net/tcp_ao; do
    make O=$output_dir $build_flags -C tools/testing/selftests/ \
	 TARGETS=$what 2> >(tee -a $tmpfile_o >&2)
done

incumbent=$(grep -i -c "\(warn\|error\)" $tmpfile_o)

echo "Building the tree with the patch"

git checkout -q $HEAD

make O=$output_dir $build_flags headers
for what in net net/forwarding net/tcp_ao; do
    make O=$output_dir $build_flags -C tools/testing/selftests/ \
	 TARGETS=$what 2> >(tee -a $tmpfile_n >&2)
done

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
