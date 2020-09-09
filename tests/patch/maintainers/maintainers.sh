#!/bin/bash
# SPDX-License-Identifier: GPL-2.0

if git diff-index --quiet --name-only HEAD~ -- MAINTAINERS; then
  echo "MAINTAINERS not touched" >&$DESC_FD
  exit 0
fi

tmpfile_o=$(mktemp)
tmpfile_n=$(mktemp)
rc=0

echo "MAINTAINERS self-test: redirect to $tmpfile_o and $tmpfile_n"

HEAD=$(git rev-parse HEAD)

git checkout -q HEAD~

echo "Checking old warning count"

./scripts/get_maintainer.pl --self-test 2> >(tee $tmpfile_o >&2)
incumbent=$(grep -i -c "\(warn\|error\)" $tmpfile_o)

echo "Checking new warning count"

git checkout -q $HEAD

./scripts/get_maintainer.pl --self-test 2> >(tee $tmpfile_n >&2)
current=$(grep -i -c "\(warn\|error\)" $tmpfile_n)

echo "Errors and warnings before: $incumbent this patch: $current" >&$DESC_FD

if [ $current -gt $incumbent ]; then
  echo "New errors added" 1>&2
  diff $tmpfile_o $tmpfile_n 1>&2

  rc=1
fi

rm $tmpfile_o $tmpfile_n

exit $rc
