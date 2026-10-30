#!/usr/bin/env bash
# 删除 setup-tap.sh 创建的 tap 接口。
#
#   sudo ./scripts/teardown-tap.sh tap1
set -euo pipefail

IFACE="${1:-tap1}"

if ! ip link show "$IFACE" >/dev/null 2>&1; then
    echo "$IFACE 不存在"
    exit 0
fi

echo "删除 tap：$IFACE"
ip link delete "$IFACE"
