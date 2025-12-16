#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Copyright 2025 NXP

HEAD=$(git rev-parse HEAD)
ncpu=$(grep -c processor /proc/cpuinfo)
rc=0

pr() {
    echo " ====== $* ======" | tee -a /dev/stderr
}

build() {
    make -j $ncpu DT_CHECKER_FLAGS=-m dt_binding_check 2>&1
}

# Only run this check if the patch touches DT binding files.
if ! git show --diff-filter=AM --pretty="" --name-only "${HEAD}" | \
    grep -q -E "^Documentation/devicetree/bindings/"
then
    echo "No DT binding files touched, skip" >&"$DESC_FD"
    exit 0
fi

# Create temporary files for logs
tmpfile_o_raw=$(mktemp)
tmpfile_n_raw=$(mktemp)
tmpfile_o_filtered=$(mktemp)
tmpfile_n_filtered=$(mktemp)
tmp_new_issues=$(mktemp)

echo "Tree base:"
git log -1 --pretty='%h ("%s")' HEAD~
echo "Now at:"
git log -1 --pretty='%h ("%s")' HEAD

# Define the filter pattern to exclude build noise from the make output.
# We only want to see the actual error/warning lines.
FILTER_PATTERN="^(make\[|  CHKDT|  DTC|  DTEX|  HOSTCC|  HOSTLD|  LEX|  LINT|  SCHEMA|  YACC|$)"

pr "Checking before the patch"
git checkout -q HEAD~

# Run the check on the parent commit
(build | tee -a "$tmpfile_o_raw") || true

# Filter and sort the output to get only relevant issue lines
grep -v -E "$FILTER_PATTERN" "$tmpfile_o_raw" | sort > "$tmpfile_o_filtered"
incumbent_total=$(wc -l < "$tmpfile_o_filtered")

pr "Checking the tree with the patch"
git checkout -q "$HEAD"

# Run the check on the new commit
(build | tee -a "$tmpfile_n_raw") || true

# Filter and sort the output
grep -v -E "$FILTER_PATTERN" "$tmpfile_n_raw" | sort > "$tmpfile_n_filtered"
current_total=$(wc -l < "$tmpfile_n_filtered")

# Compare the filtered lists to find new and fixed issues
# Use comm to find fixed issues (lines only in the old log, column 1).
fixed_issues_count=$(comm -23 "$tmpfile_o_filtered" "$tmpfile_n_filtered" | wc -l)

# Use comm to find new issues (lines only in the new log, column 2)
# and save them for later display.
comm -13 "$tmpfile_o_filtered" "$tmpfile_n_filtered" > "$tmp_new_issues"
new_issues_count=$(wc -l < "$tmp_new_issues")

echo "Issues before: $incumbent_total, after: $current_total" \
    "(Fixed: $fixed_issues_count, New: $new_issues_count)" >&"$DESC_FD"

if [ "$new_issues_count" -gt 0 ]; then
    echo "New issues added:" 1>&2
    # Print the new issues we saved
    cat "$tmp_new_issues" 1>&2
    rc=1
elif [ "$fixed_issues_count" -gt 0 ]; then
    echo "Patch fixed $fixed_issues_count issue(s)." >&2
    # No new issues, and some were fixed. This is a success.
fi

rm "$tmpfile_o_raw" "$tmpfile_n_raw" "$tmpfile_o_filtered" "$tmpfile_n_filtered" \
    "$tmp_new_issues"

exit $rc
