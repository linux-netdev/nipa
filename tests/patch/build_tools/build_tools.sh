#!/bin/bash
# SPDX-License-Identifier: GPL-2.0

output_dir=build_tools/
ncpu=$(grep -c processor /proc/cpuinfo)
build_flags="-Oline -j $ncpu"
tmpfile_o=$(mktemp)
tmpfile_n=$(mktemp)
rc=0

pr() {
    echo " ====== $@ ======" | tee -a /dev/stderr
}

# If it doesn't touch tools/ or include/, don't bother
if ! git diff --name-only HEAD~ | grep -q -E "^(include)|(tools)/"; then
    echo "No tools touched, skip" >&$DESC_FD
    exit 0
fi

# Looks like tools inherit WERROR, otherwise
make O=$output_dir allmodconfig
./scripts/config --file $output_dir/.config -d werror

echo "Using $build_flags redirect to $tmpfile_o and $tmpfile_n"

HEAD=$(git rev-parse HEAD)

echo "Tree base:"
git log -1 --pretty='%h ("%s")' HEAD~
echo "Now at:"
git log -1 --pretty='%h ("%s")' HEAD

# These are either very slow or don't build
export SKIP_TARGETS="bpf dt landlock livepatch lsm user_events mm powerpc"

pr "Cleaning"
make O=$output_dir $build_flags -C tools/testing/selftests/ clean

# Hard-clean YNL, too, otherwise YNL-related build problems may be masked
make -C tools/net/ynl/ distclean

pr "Baseline building the tree"
git checkout -q HEAD~
make O=$output_dir $build_flags headers
make O=$output_dir $build_flags -C tools/testing/selftests/
git checkout -q $HEAD

pr "Building the tree before the patch"
git checkout -q HEAD~

make O=$output_dir $build_flags headers
make O=$output_dir $build_flags -C tools/testing/selftests/ \
     2> >(tee -a $tmpfile_o >&2)

incumbent=$(grep -i -c "\(warn\|error\)" $tmpfile_o)

pr "Checking if tree is clean"
git status -s 1>&2
incumbent_dirt=$(git status -s | grep -c '^??')

pr "Building the tree with the patch"
git checkout -q $HEAD

make O=$output_dir $build_flags headers
make O=$output_dir $build_flags -C tools/testing/selftests/ \
     2> >(tee -a $tmpfile_n >&2)

current=$(grep -i -c "\(warn\|error\)" $tmpfile_n)

pr "Checking if tree is clean"
git status -s 1>&2
current_dirt=$(git status -s | grep -c '^??')

echo "Errors and warnings before: $incumbent (+$incumbent_dirt) this patch: $current (+$current_dirt)" >&$DESC_FD

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

if [ $current_dirt -gt $incumbent_dirt ]; then
    echo "New untracked files added" 1>&2

    rc=1
fi

rm $tmpfile_o $tmpfile_n

exit $rc
