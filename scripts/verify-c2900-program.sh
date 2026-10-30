#!/usr/bin/env bash
# C2900 非网络程序插桩验证（M9 启动 stub + snapshot_trigger）。
#
# 用法：
#   ./verify-c2900-program.sh               # 默认 5 轮
#   ./verify-c2900-program.sh --rounds 3
#   ./verify-c2900-program.sh --build       # 先增量构建 QEMU（包含 snapshot_trigger）
#
# 不需要 guest 网络、tap 或 sudo；与 C2900 runtime 共用 /tmp FIFO/shm，不能并行。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD=0
ARGS=()
for arg in "$@"; do
    if [ "$arg" = "--build" ]; then
        BUILD=1
    else
        ARGS+=("$arg")
    fi
done

if [ "$BUILD" = 1 ]; then
    "$ROOT/scripts/build-qemu.sh"
fi

exec python3 "$ROOT/runtime/c2900-program/verify_stub.py" ${ARGS[@]+"${ARGS[@]}"}
