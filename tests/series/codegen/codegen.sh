#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (c) 2023 Meta Platforms, Inc. and affiliates

./tools/net/ynl/ynl-regen.sh -f

if git diff --quiet; then
  echo "Generated files up to date" >&$DESC_FD
  rc=0
else
  echo "Tree is dirty after regen" >&$DESC_FD
  git status --porcelain
  rc=1
fi

exit $rc
