#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# Copyright (C) 2019 Netronome Systems, Inc.

cc="ccache gcc"
output_dir=build_allmodconfig/
ncpu=$(grep -c processor /proc/cpuinfo)
build_flags="-j $ncpu W=1 C=1"

make CC="$cc" O=$output_dir allmodconfig
make CC="$cc" O=$output_dir $build_flags
