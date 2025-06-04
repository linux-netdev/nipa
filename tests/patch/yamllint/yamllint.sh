#!/bin/bash
# SPDX-License-Identifier: GPL-2.0

HEAD=$(git rev-parse HEAD)
rc=0

pr() {
    echo " ====== $* ======" | tee -a /dev/stderr
}

# If it doesn't touch .yaml files, don't bother. Ignore deleted.
if ! git show --diff-filter=AM --pretty="" --name-only "${HEAD}" | grep -q -E "\.yaml$"
then
    echo "No YAML files touched, skip" >&"$DESC_FD"
    exit 0
fi

tmpfile_o=$(mktemp)
tmpfile_n=$(mktemp)

echo "Redirect to $tmpfile_o and $tmpfile_n"

echo "Tree base:"
git log -1 --pretty='%h ("%s")' HEAD~
echo "Now at:"
git log -1 --pretty='%h ("%s")' HEAD

pr "Checking before the patch"
git checkout -q HEAD~

# Also ignore created, as not present in the parent commit
for f in $(git show --diff-filter=M --pretty="" --name-only "${HEAD}" | grep -E "\.yaml$"); do
    yamllint "$f" | tee -a "$tmpfile_o"
done

incumbent=$(grep -i -c " error " "$tmpfile_o")
incumbent_w=$(grep -i -c " warning " "$tmpfile_o")

pr "Checking the tree with the patch"
git checkout -q "$HEAD"

for f in $(git show --diff-filter=AM --pretty="" --name-only "${HEAD}" | grep -E "\.yaml$"); do
    yamllint "$f" | tee -a "$tmpfile_n"
done

current=$(grep -i -c " error " "$tmpfile_n")
current_w=$(grep -i -c " warning " "$tmpfile_n")

echo "Errors before: $incumbent (+warn: $incumbent_w) this patch: $current (+warn: $current_w)" >&"$DESC_FD"

if [ "$current_w" -gt "$incumbent_w" ]; then
    echo "New warnings added" 1>&2

    rc=250
fi

if [ "$current" -gt "$incumbent" ]; then
    echo "New errors added" 1>&2
    diff -U 0 "$tmpfile_o" "$tmpfile_n" 1>&2

    rc=1
fi

rm "$tmpfile_o" "$tmpfile_n"

exit $rc
