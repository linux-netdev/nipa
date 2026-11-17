#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Generic wrapper to execute commands from the container: create symlinks named
# after the commands to wrap -- e.g. 'make' and 'vng' -- pointing to this script
# in a directory placed at the beginning of $PATH.

cmd=("$(basename "${0}")")

# 'vng --run' needs root privileges to start some VM in the container
if [ "${cmd[0]}" = "vng" ]; then
	for arg in "${@}"; do
		[ "${arg}" = "--" ] && break
		if [ "${arg}" = "--run" ] || [ "${arg}" = "-r" ]; then
			cmd=(sudo "${cmd[@]}")
			break
		fi
	done
fi

exec "$(dirname "$(realpath "${0}")")/run.sh" "${cmd[@]}" "${@}"
