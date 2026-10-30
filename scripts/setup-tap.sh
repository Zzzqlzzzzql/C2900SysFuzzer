#!/usr/bin/env bash
# 为 C2900 SSH/HTTP 等协议 fuzz 创建独立 tap 接口。
#
# 用法（需要 sudo，建议用户在终端执行）：
#   sudo ./scripts/setup-tap.sh tap1
#   NET_MODE=tap TAP_IFACE=tap1 ./scripts/protocol.sh prepare ssh
#
# 不会删除已有接口；如果 tap1 已存在会直接退出，避免误删其他项目的 tap0。
set -euo pipefail

IFACE="${1:-tap1}"
CIDR="${TAP_HOST_CIDR:-172.16.100.1/24}"
# sudo 会把 $USER 置成 root；tap 必须交给调用 sudo 的普通用户，
# 否则 QEMU（以该用户运行）无法打开 /dev/net/tun。
OWNER="${SUDO_USER:-$USER}"

if ip link show "$IFACE" >/dev/null 2>&1; then
    echo "错误：$IFACE 已存在；拒绝删除/覆盖。请换一个名字或先手动清理。" >&2
    exit 1
fi

echo "创建 tap：$IFACE（host $CIDR，owner $OWNER）"
ip tuntap add dev "$IFACE" mode tap user "$OWNER"
ip link set "$IFACE" up
ip addr add "$CIDR" dev "$IFACE"
ip -brief addr show "$IFACE"
