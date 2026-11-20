#!/bin/sh
# SPDX-License-Identifier: GPL-2.0

# Expect we were booted into a virtme-ng VM with "--net loop"

# Interface names depend on the QEMU machine type and the PCI slots
virtio_ifcs=$(for ifc in /sys/class/net/*; do
    drv=$(readlink -f "$ifc/device/driver")
    [ "${drv##*/}" = virtio_net ] || continue
    echo "$(readlink -f "$ifc/device") ${ifc##*/}"
done | sort | cut -d' ' -f2)

if [ "$(echo "$virtio_ifcs" | wc -l)" -ne 2 ]; then
    echo "Error: expected 2 virtio interfaces, found: $(echo $virtio_ifcs)"
    exit 1
fi

IFC0=$(echo "$virtio_ifcs" | sed -n 1p)
IFC1=$(echo "$virtio_ifcs" | sed -n 2p)

ip netns add ns-remote
ip link set dev $IFC1 netns ns-remote
export REMOTE_TYPE=netns
export REMOTE_ARGS=ns-remote

ip                  link set dev $IFC0 up
ip -netns ns-remote link set dev $IFC1 up
export NETIF=$IFC0

ip                  addr add dev $IFC0 192.0.3.1/24
ip -netns ns-remote addr add dev $IFC1 192.0.3.2/24
export  LOCAL_V4=192.0.3.1
export REMOTE_V4=192.0.3.2

ip                  addr add dev $IFC0 2001:db8:1::1/64 nodad
ip -netns ns-remote addr add dev $IFC1 2001:db8:1::2/64 nodad
export  LOCAL_V6=2001:db8:1::1
export REMOTE_V6=2001:db8:1::2

sysctl -w net.ipv6.conf.$IFC0.keep_addr_on_down=1
# We don't bring remote down, it'd break remote via SSH

sleep 1
