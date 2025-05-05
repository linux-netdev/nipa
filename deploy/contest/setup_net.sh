#!/bin/bash

for i in `seq 0 3`; do
    sudo ip tuntap add name tap$((i * 2    )) mode tap multi_queue group virtme
    sudo ip tuntap add name tap$((i * 2 + 1)) mode tap multi_queue group virtme

    sudo ip li add name br$i type bridge
    sudo ip link set dev tap$((i * 2    )) master br$i
    sudo ip link set dev tap$((i * 2 + 1)) master br$i

    sudo ip link set dev br$i up
    sudo ip link set dev tap$((i * 2    )) up
    sudo ip link set dev tap$((i * 2 + 1)) up
done
