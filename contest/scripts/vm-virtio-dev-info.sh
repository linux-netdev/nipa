#!/bin/sh
# SPDX-License-Identifier: GPL-2.0

qver=$(qemu-system-x86_64 --version | head -1)

echo '{"driver":"virtio_net","versions":{"fixed":{},"stored":{},"running":{"fw":"'"$qver"'"}}}'
