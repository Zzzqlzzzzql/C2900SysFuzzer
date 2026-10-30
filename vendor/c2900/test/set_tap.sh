#!/usr/bin/env bash
set -e

if ip link show tap0 >/dev/null 2>&1; then
	sudo ip link delete tap0
fi

sudo ip tuntap add dev tap0 mode tap user $USER
sudo ip link set tap0 up
sudo ip addr add 172.16.100.1/24 dev tap0
