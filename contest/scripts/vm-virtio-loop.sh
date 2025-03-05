#!/bin/sh
# SPDX-License-Identifier: GPL-2.0

# Expect we were booted into a virtme-ng VM with "--net loop"

for ifc in eth0 eth1; do
    if ! ethtool -i "$ifc" | grep -q virtio; then
	echo "Error: $ifc is not virtio"
	exit 1
    fi
done

ip netns add ns-remote
ip link set dev eth1 netns ns-remote
export REMOTE_TYPE=netns
export REMOTE_ARGS=ns-remote

ip                  link set dev eth0 up
ip -netns ns-remote link set dev eth1 up
export NETIF=eth0

ip                  addr add dev eth0 192.0.2.1/24
ip -netns ns-remote addr add dev eth1 192.0.2.2/24
export  LOCAL_V4=192.0.2.1
export REMOTE_V4=192.0.2.2

ip                  addr add dev eth0 2001:db8::1/64 nodad
ip -netns ns-remote addr add dev eth1 2001:db8::2/64 nodad
export  LOCAL_V6=2001:db8::1
export REMOTE_V6=2001:db8::2

sysctl -w net.ipv6.conf.eth0.keep_addr_on_down=1
# We don't bring remote down, it'd break remote via SSH

sleep 1
