#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Generic wrapper to execute commands from the container: create symlinks named
# after the commands to wrap -- e.g. 'make' and 'vng' -- pointing to this script
# in a directory placed at the beginning of $PATH.

exec "$(dirname "$(realpath "${0}")")/run.sh" "$(basename "${0}")" "${@}"
