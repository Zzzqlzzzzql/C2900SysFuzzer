#!/usr/bin/env bash
# 端到端冒烟：启动 QEMU → 等 banner → 0x01 快照 → 2 轮 0x02/0x03 → 验证稳定 → 停止。
#
# 用法：
#   ./smoke-test.sh [m7|m9] [banner_timeout_seconds]
# 默认：m7，1800s（实测插桩启动到 banner 约 23 分钟；留足余量）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KIND="${1:-m7}"
TIMEOUT="${2:-1800}"
PKG="${C2900_PKG:-$ROOT/vendor/c2900}"
START="$ROOT/runtime/start_c2900_fuzz.sh"
CLIENT="$ROOT/runtime/fuzz_client.py"

case "$KIND" in
    m7)
        export C2900_KERNEL="$PKG/firmware/C2900-UN.150.BIN"
        export C2900_FUZZ_CONFIG="$ROOT/runtime/c2900-ios-m7.json"
        ;;
    m9)
        export C2900_KERNEL="$PKG/firmware/c2900-universalk9-mz.SPA.150-1.M9.bin"
        export C2900_FUZZ_CONFIG="$ROOT/runtime/c2900-ios.json"
        ;;
    *)
        echo "用法：$0 [m7|m9] [timeout]" >&2
        exit 2
        ;;
esac

cleanup() { "$START" stop >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== 启动（$KIND） =="
"$START" start
echo "== 等待 banner（最多 ${TIMEOUT}s） =="
"$START" wait "$TIMEOUT"
echo "== IPC =="
"$CLIENT" wait --timeout 30
echo "== 快照/轮次 =="
"$CLIENT" save
"$CLIENT" verify --rounds 2
echo
echo "冒烟测试通过。日志：$ROOT/runtime/run/Logs/"
