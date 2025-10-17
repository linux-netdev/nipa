#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.
# Copyright (c) 2020 Facebook

# Cleanup warnings by merging lines which end with ':' to their referenced
# line content, and sorting the output carefully to ensure that the entries
# are stable w.r.t code being re-ordered.
#
# In particular, we sort first by the warning description and then by the
# filename path without its line number element.
#
# This assumes all lines are "Warning:" or "Error:" and that file names do not
# have whitespace.
function merge_and_sort() {
  awk '{ if ($0 ~ /:$/) { ORS="" } else { ORS="\n" }; print }' | \
    sort -s -k 3 | sort -s -t ':' -k 2,2
}

# Strip out line number information from warnings and errors
function strip_line_numbers() {
  sed -e 's@^\(\([Ww]arning\|[Ee]rror\):\s*[/a-zA-Z0-9_.-]*.[ch]\):[0-9]*@\1@' "${@}"
}

# Diff output, printing a sed range expression for any removed lines.
function old_lines_to_range() {
  diff --line-format="" --old-group-format="%df,%dlp;" "${@}"
}

# Diff output, printing a sed range expression for any newly added lines.
function new_lines_to_range() {
  diff --line-format="" --new-group-format="%dF,%dLp;" "${@}"
}

tmpfile_o=$(mktemp)
tmpfile_n=$(mktemp)
tmpfile_so=$(mktemp)
tmpfile_sn=$(mktemp)
tmpfile_rm=$(mktemp)
tmpfile_new=$(mktemp)
rc=0

files_mod=$(git show --diff-filter=M  --pretty="" --name-only "HEAD")
files_all=$(git show --diff-filter=AM --pretty="" --name-only "HEAD")

HEAD=$(git rev-parse HEAD)

echo "Checking the tree before the patch"
git checkout -q HEAD~

echo "Before patch:" >&2
./scripts/kernel-doc -Wall -none $files_mod 2>&1 | merge_and_sort | tee $tmpfile_o >&2

strip_line_numbers $tmpfile_o > $tmpfile_so

incumbent=$(grep -v 'Error: Cannot open file ' $tmpfile_o | wc -l)

echo "Checking the tree with the patch"

git checkout -q $HEAD

echo "After patch:" >&2
./scripts/kernel-doc -Wall -none $files_all 2>&1 | merge_and_sort | tee $tmpfile_n >&2

strip_line_numbers $tmpfile_n > $tmpfile_sn

current=$(grep -v 'Error: Cannot open file ' $tmpfile_n | wc -l)

# Find removed warnings
removed_range=$(old_lines_to_range ${tmpfile_so} ${tmpfile_sn})
sed -n -e "${removed_range}" ${tmpfile_o} > ${tmpfile_rm}

removed=$(grep -v 'Error: Cannot open file ' $tmpfile_rm | wc -l)

# Find new warnings
new_range=$(new_lines_to_range ${tmpfile_so} ${tmpfile_sn})
sed -n -e "${new_range}" ${tmpfile_n} > ${tmpfile_new}

new_warnings=$(grep -v 'Error: Cannot open file ' $tmpfile_new | wc -l)

echo "Errors and warnings before: $incumbent This patch: $current" >&$DESC_FD
echo "New: $new_warnings Removed: $removed" >&$DESC_FD

if [ $removed -gt 0 ]; then
  echo "Warnings removed:" 1>&2
  sort -V $tmpfile_rm 1>&2

  echo "Per-file breakdown:" 1>&2
  grep -i "\(warn\|error\)" $tmpfile_rm | sed -n 's@^\([Ww]arning\|[Ee]rror\):\s*\([/a-zA-Z0-9_.-]*.[ch]\):.*@\2@p' | sort | uniq -c 1>&2
fi

if [ $new_warnings -gt 0 ]; then
  echo "New warnings added:" 1>&2
  sort -V $tmpfile_new 1>&2

  echo "Per-file breakdown:" 1>&2
  grep -i "\(warn\|error\)" $tmpfile_new | sed -n 's@^\([Ww]arning\|[Ee]rror\):\s*\([/a-zA-Z0-9_.-]*.[ch]\):.*@\2@p' | sort | uniq -c 1>&2

  rc=1
fi

rm $tmpfile_o $tmpfile_n $tmpfile_so $tmpfile_sn

exit $rc
