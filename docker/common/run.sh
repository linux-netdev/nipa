#!/bin/bash -e
# SPDX-License-Identifier: GPL-2.0

: "${INPUT_PREFIX:="ghcr.io/linux-netdev"}"
: "${INPUT_TAG:="latest"}"

SCRIPT_DIR=$(cd "$(dirname "${0}")" && pwd)
DIR_NAME="$(basename "${SCRIPT_DIR}")"
CCACHE_DIR="${SCRIPT_DIR}/.ccache"
mkdir -p "${CCACHE_DIR}"
ARGS=(
	-v "${CCACHE_DIR}:/home/nipa/.ccache:rw"
	-v "${PWD}:${PWD}:rw"
	-w "${PWD}"
	-u "${RUID:-$(id -u)}:${RGID:-$(id -g)}"
	--group-add "$(grep "^kvm:" /etc/group | cut -d: -f3)"
	--rm
	-i
	--privileged
)
test -t 1 && ARGS+=("-t")

if [ "${1}" = "--pull" ]; then
	ARGS+=(--pull always)
	shift
fi

DOCKER_IMG="${INPUT_PREFIX}/nipa-${DIR_NAME}:${INPUT_TAG}"
test -t 2 && echo "Using ${DOCKER_IMG} image" >&2

# $1: basedir
add_git_worktree() {
	local wt
	if [ -f "${1}/.git" ]; then
		wt="$(realpath "$(git -C "${1}" rev-parse --git-common-dir)")"
		ARGS+=(-v "${wt}:${wt}:rw")
	fi
}

add_git_worktree .
# trying to be smart: mounting directories passed in argument, or as values,
# e.g. 'INSTALL_PATH=<dir>' or '--opt=<dir>'
for arg in "${@}"; do
	[ -d "${arg}" ] || arg="${arg##*=}"
	if [ -d "${arg}" ]; then
		d="$(realpath "${arg}")"
		ARGS+=(-v "${d}:${d}:rw")
		add_git_worktree "${arg}"
	fi
done

docker run "${ARGS[@]}" "${DOCKER_IMG}" "${@:-bash}"
