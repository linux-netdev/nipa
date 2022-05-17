#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Check if the shell selftest scripts are in correspond Makefile

rt=0

files=$(git show --pretty="" --name-only -- tools/testing/selftests*.sh)
if [ -z "$files" ]; then
	echo "No net selftest shell script" >&$DESC_FD
	exit $rt
fi

for file in $files; do
	f=$(basename $file)
	d=$(dirname $file)
	if [ -f "${d}/Makefile" ] && ! grep -P "[\t| ]${f}" ${d}/Makefile; then
		echo "Script ${f} not found in ${d}/Makefile" >&$DESC_FD
		rt=1
	fi
done

[ ${rt} -eq 0 ] && echo "net selftest script(s) already in Makefile" >&$DESC_FD

exit $rt
