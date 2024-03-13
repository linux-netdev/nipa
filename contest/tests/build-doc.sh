#!/bin/bash
# SPDX-License-Identifier: GPL-2.0

tmpfile_o=$(mktemp)
tmpfile_n=$(mktemp)
rc=0

# rev-parse wants the branch with remote path
full_branch=$(git branch -a --list "*$BRANCH" | tail -1)

echo " === Start ==="
echo "Base: $BASE"
echo "Branch: $BRANCH ($(git rev-parse $full_branch))"
echo

echo " === Building the base tree ==="
git checkout -q $BASE
make cleandocs
make -Oline htmldocs 2> >(tee $tmpfile_o >&2) || exit 1

echo " === Building the new tree ==="
git checkout -q $BRANCH
make cleandocs
make -Oline htmldocs 2> >(tee $tmpfile_n >&2) || exit 1

incumbent=$(grep -i -c "\(warn\|error\)" $tmpfile_o)
current=$(grep -i -c "\(warn\|error\)" $tmpfile_n)

if [ $current -gt $incumbent ]; then
  echo "New errors added" 1>&2
  diff -U 0 $tmpfile_o $tmpfile_n 1>&2
  rc=1
fi

echo
echo " === Summary === "
echo "Incumbent: $incumbent"
echo "Current:   $current"
echo "Result: $rc"

rm $tmpfile_o $tmpfile_n

exit $rc
