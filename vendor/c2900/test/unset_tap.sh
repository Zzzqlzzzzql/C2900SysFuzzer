#!/usr/bin/env bash
set -e

if ip link show tap0 >/dev/null 2>&1; then
	sudo ip link delete tap0
fi
