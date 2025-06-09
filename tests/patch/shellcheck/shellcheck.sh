#!/bin/bash
# SPDX-License-Identifier: GPL-2.0

HEAD=$(git rev-parse HEAD)
rc=0

pr() {
    echo " ====== $* ======" | tee -a /dev/stderr
}

# If it doesn't touch .sh files, don't bother. Ignore deleted.
if ! git show --diff-filter=AM --pretty="" --name-only "${HEAD}" | grep -q -E "\.sh$"
then
    echo "No shell scripts touched, skip" >&"$DESC_FD"
    exit 0
fi

shellcheck --version || exit 1

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
for f in $(git show --diff-filter=M --pretty="" --name-only "${HEAD}" | grep -E "\.sh$"); do
    (
	sha=$(echo "$f" | sha256sum | awk '{print $1}')
	echo "Checking $f - $sha"
	echo

	cd "$(dirname "$f")" || exit 1
	sha="${tmpfile_o}_${sha}"
	rm -f "${sha}"
	shellcheck -x "$(basename "$f")" | tee -a "${tmpfile_o}" "${sha}"
	echo
    )
done

# ex: SC3045 (warning): In POSIX sh, printf -v is undefined.
# severity: error, warning, info, style
incumbent=$(grep -c " (error):" "$tmpfile_o")
incumbent_w=$(grep -c " (warning):" "$tmpfile_o")

pr "Checking the tree with the patch"
git checkout -q "$HEAD"

for f in $(git show --diff-filter=AM --pretty="" --name-only "${HEAD}" | grep -E "\.sh$"); do
    (
	sha=$(echo "$f" | sha256sum | awk '{print $1}')
	echo "Checking $f - $sha"
	echo

	cd "$(dirname "$f")" || exit 1
	sha="${tmpfile_n}_${sha}"
	rm -f "${sha}"
	shellcheck -x "$(basename "$f")" | tee -a "${tmpfile_n}" "${sha}"
	echo
    )
done

# severity: error, warning, info, style
current=$(grep -c " (error):" "$tmpfile_n")
current_w=$(grep -c " (warning):" "$tmpfile_n")

# if a file was compliant before or is new, mark everything as error to keep it good.
for f in "${tmpfile_n}_"*; do
    [ ! -s "${f}" ] && continue # still compliant

    sha="${f:${#tmpfile_n}+1}"
    old="${tmpfile_o}_${sha}"
    [ -s "${old}" ] && continue # wasn't compliant

    fname=$(head -n2 "${f}" | tail -n1 | sed "s/^In \(\S\+\.sh\) line [0-9]\+:/\1/g")
    if [ -f "${old}" ]; then
        echo "${fname} was shellcheck compliant, not anymore" 1>&2
    else
        echo "${fname} is a new file, but not shellcheck compliant" 1>&2
    fi

    extra=$(grep -c -E " \((warning|info|style)\):" "${f}")
    current=$((current + extra))
done

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

rm "$tmpfile_o"* "$tmpfile_n"*

exit $rc
