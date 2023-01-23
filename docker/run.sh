#!/bin/bash

set -euo pipefail

. ./config

echo >&2 Build nipa docker image...
docker build --build-arg=nipauid=`id -u` -t nipa-local .

echo >&2 Removing previous nipa-run...
rm -rf ./nipa-run
mkdir -p ./nipa-run/tmp ./nipa-run/patatt ./ccache

echo >&2 Creating CoW tree...
if ! cp -a --reflink=always $NIPA_TREE ./nipa-run/tree; then
    echo >&2 Falling back to git clone and hardlinks...
    rm -rf ./nipa-run/tree
    git clone -b nipa-local $NIPA_TREE ./nipa-run/tree
fi
# nipa doesn't work if HEAD is pointing to nipa-local, so generate a
# guaranteed random branch name as HEAD.  This always happens on
# non-reflink filesystems, but can even happen on reflink if the
# developer left his tree standing on nipa-local.
( cd ./nipa-run/tree ; git checkout -b nipa-local-tmp-$(cat /proc/sys/kernel/random/uuid | md5sum | head -c 10) )

# This username hacking is necessary, because the patch receiving side
# in nipa adds an extra signed-off even if it was already there,
# resulting in failing checkpatch.  This is happening, because git-am
# is ran with "git am -s".
(
    cd ./nipa-run/tree
    git config user.email nipa@local
    git config user.name NipaLocal
)
echo >&2 Running nipa in Docker...
# We try to have everything read-only that is not necessary to write,
# to make sure that we can monitor cases when some nipa tests write
# outside of the run directory or of /tmp.
docker run $DOCKER_FLAGS -it --rm --user=nipa \
       --read-only \
       -v $PWD/nipa-run/tmp:/tmp \
       -v $PWD/..:/nipa:ro \
       -v $PWD/nipa-run/patatt:/home/nipa/.local/share/patatt \
       -v $PWD/nipa-run/patatt:/root/.local/share/patatt \
       -v $PWD/nipa.config:/nipa.config:ro \
       -v $PWD/nipa-run:/nipa-run \
       -v $NIPA_PATCHES:/nipa-patches:ro \
       -v $PWD/ccache:/home/nipa/.ccache \
       -v $PWD/ccache:/root/.ccache \
       --name nipa-local nipa-local \
       /nipa/ingest_mdir.py --mdir /nipa-patches --tree /nipa-run/tree --tree-name $NIPA_TREE_NAME --tree-branch nipa-local
