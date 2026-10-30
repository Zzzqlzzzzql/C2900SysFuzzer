#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if tmux has-session -t qemu 2>/dev/null; then
	tmux kill-session -t qemu
fi

"$SCRIPT_DIR/unset_tap.sh"
