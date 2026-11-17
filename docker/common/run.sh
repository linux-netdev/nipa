#!/bin/bash -e
# SPDX-License-Identifier: GPL-2.0

: "${INPUT_PREFIX:="ghcr.io/linux-netdev"}"
: "${INPUT_TAG:="latest"}"

# Rootless Podman: keep the same UID/GID in the container, to be able to write
# to the mounted directories. This variable is ignored by Docker.
if [ "$(id -u)" != 0 ]; then
	export PODMAN_USERNS="${PODMAN_USERNS:-keep-id}"
fi

SCRIPT_DIR=$(cd "$(dirname "${0}")" && pwd)
DIR_NAME="$(basename "${SCRIPT_DIR}")"
CCACHE_DIR="${SCRIPT_DIR}/.ccache"
CONTAINER_NAME="nipa-${DIR_NAME}-${$}-${RANDOM}"
mkdir -p "${CCACHE_DIR}"
ARGS=(
	--name "${CONTAINER_NAME}"
	-v "${CCACHE_DIR}:/home/nipa/.ccache:rw"
	-v "${PWD}:${PWD}:rw"
	-w "${PWD}"
	-u "${RUID:-$(id -u)}:${RGID:-$(id -g)}"
	--group-add "$(grep "^kvm:" /etc/group | cut -d: -f3)"
	--rm
	-i
	-e TERM
	--init
	--log-driver none
	--privileged
)
test -t 1 && ARGS+=("-t")

# Extra options for 'docker run', split on whitespace, e.g. '--network host'
if [ -n "${DOCKER_EXTRA_ARGS:-}" ]; then
	read -r -a EXTRA_ARGS <<< "${DOCKER_EXTRA_ARGS}"
	ARGS+=("${EXTRA_ARGS[@]}")
fi

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

# $@: commands, returns 0 if one of them would execute a wrapper.sh script
is_wrapped() {
	local cmd path
	for cmd in "${@}"; do
		path="$(command -v "${cmd}")" || continue
		[ "$(basename "$(realpath "${path}")")" = "wrapper.sh" ] && return 0
	done
	return 1
}

# When the client is killed with SIGKILL -- e.g. by contest/remote/lib/vm.py
# when a VM doesn't stop -- the container is not stopped. A detached process,
# not part of the client's process tree, removes it when the client is gone.
# It is not started if it would call itself, via wrapped commands.
if is_wrapped setsid bash sleep docker; then
	echo "Warning: wrapped commands, the container will not be removed if the client is killed" >&2
else
	# shellcheck disable=SC2016 # expanded by the detached process
	setsid -f bash -c '
		trap "docker rm -f ${2}; exit" HUP INT TERM
		while kill -0 "${1}"; do sleep 1; done
		docker rm -f "${2}"' cleanup "${$}" "${CONTAINER_NAME}" &>/dev/null </dev/null
fi

# exec: signals sent to this PID are forwarded by the client to the container
exec docker run "${ARGS[@]}" "${DOCKER_IMG}" "${@:-bash}"
