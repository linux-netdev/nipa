#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2023 Meta Platforms, Inc. and affiliates

HEAD=$(git rev-parse HEAD)
ncpu=$(grep -c processor /proc/cpuinfo)
tmpfile=$(mktemp)
rc=0

##################################################################
echo " ====== 1/ Test regeneration ======"
./tools/net/ynl/ynl-regen.sh -f

if git diff --quiet; then
  echo "Generated files up to date;" >&$DESC_FD
else
  echo "Tree is dirty after regen;" >&$DESC_FD
  git status --porcelain
  rc=1
fi

##################################################################
echo " ====== 2/ Test build ======"
make -C tools/net/ynl/ distclean
if ! make -C tools/net/ynl/ -j $ncpu 2> >(tee $tmpfile >&2); then
  echo "build failed;" >&$DESC_FD
  rc=1
fi

cnt=$(grep -i -c "\(warn\|error\)" $tmpfile)

if [ $cnt -eq 0 ]; then
  echo "no warnings/errors;" >&$DESC_FD
else
  echo "build has $cnt warnings/errors;" >&$DESC_FD
  rc=1
fi

##################################################################
echo " ====== 3/ Generate diffs for user codegen ======"

mkdir $RESULTS_DIR/old-code
git checkout -q $BRANCH_BASE
make -C tools/net/ynl/generated/ distclean
make -C tools/net/ynl/generated/ -j $ncpu
cp tools/net/ynl/generated/*.[ch] $RESULTS_DIR/old-code/

mkdir $RESULTS_DIR/new-code
git checkout -q $HEAD
make -C tools/net/ynl/generated/ distclean
make -C tools/net/ynl/generated/ -j $ncpu
cp tools/net/ynl/generated/*.[ch] $RESULTS_DIR/new-code/

git diff --no-index --stat \
    $RESULTS_DIR/old-code/ $RESULTS_DIR/new-code/ > $RESULTS_DIR/diff-stat
git diff --no-index \
    $RESULTS_DIR/old-code/ $RESULTS_DIR/new-code/ > $RESULTS_DIR/diff

git diff --no-index --exit-code \
    $RESULTS_DIR/old-code/ $RESULTS_DIR/new-code/ >> /dev/null
if [ $? -eq 0 ]; then
  echo "no diff in generated;" >&$DESC_FD
else
  echo "GEN HAS DIFF $(cat $RESULTS_DIR/diff-stat | tail -1);" >&$DESC_FD
fi

rm -rf $RESULTS_DIR/old-code/ $RESULTS_DIR/new-code/

rm $tmpfile

exit $rc
