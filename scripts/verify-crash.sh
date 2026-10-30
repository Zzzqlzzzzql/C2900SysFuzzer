#!/usr/bin/env bash
# 验证 L0 TB 哨兵 → shm 0x2001 → AFLNet waitpid 状态的崩溃反馈链路。
#
# 用 M7 + -S 缩短验证时间：
#   1. crash_mode=on，panic_addrs=["0x81000000"]：cont 后应写 exit=0x0b(SIGSEGV)
#   2. crash_mode=log：只记录 crash candidate，shm 仍为 0xDEADBEEF
#
# 这不是 IOS 真实崩溃；真实崩溃的哨兵地址需要用 crash_mode=log + 已知崩溃标定。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="${C2900_PKG:-$ROOT/vendor/c2900}"
START="$ROOT/runtime/start_c2900_fuzz.sh"
PROBE="$ROOT/runtime/coverage_probe.py"
KERNEL="${C2900_KERNEL:-$PKG/firmware/C2900-UN.150.BIN}"
WAIT="${1:-4}"

fail() { echo "FAIL: $*" >&2; exit 1; }
stop_all() {
    C2900_FUZZ_RUNDIR="$RUNDIR" "$START" stop >/dev/null 2>&1 || true
    rm -rf "$RUNDIR"
}

run_case() {
    local name="$1" cfg="$2" expect_exit="$3"
    RUNDIR="/tmp/c2900-crash-$name"
    rm -rf "$RUNDIR"
    echo "== case: $name =="
    C2900_QEMU="${C2900_QEMU:-$ROOT/vendor/qemu/bin/qemu-system-mips64}" \
    C2900_KERNEL="$KERNEL" \
    C2900_FUZZ_CONFIG="$cfg" \
    C2900_FUZZ_RUNDIR="$RUNDIR" \
    C2900_FUZZ_START_PAUSED=1 \
        "$START" start >/dev/null
    "$PROBE" wait 30 >/dev/null || fail "$name: shm 未就绪"

    # 暂停时必须是超时哨兵
    local before
    before="$("$PROBE" stats | sed -n 's/.*exit=\([^ ]*\).*/\1/p')"
    [ "$before" = "0xdeadbeef" ] || fail "$name: 暂停时 exit=$before，应为 0xdeadbeef"

    printf 'cont\n' | timeout 5 socat - UNIX-CONNECT:"$RUNDIR/monitor.sock" \
        >/dev/null 2>&1 || true
    sleep "$WAIT"

    local after
    after="$("$PROBE" stats | sed -n 's/.*exit=\([^ ]*\).*/\1/p')"
    local logf="$RUNDIR/Logs/coverage.log"
    grep -q "crash candidate: reason=panic_addr" "$logf" \
        || fail "$name: coverage.log 没有 crash candidate"
    [ "$after" = "$expect_exit" ] \
        || fail "$name: cont 后 exit=$after，应为 $expect_exit"

    echo "  paused exit=$before -> after=$after"
    grep "crash candidate" "$logf" | tail -1

    mkdir -p "$ROOT/logs/verification-2026-10-19/crash"
    cp "$logf" "$ROOT/logs/verification-2026-10-19/crash/coverage-$name.log"

    stop_all
    rm -rf "$RUNDIR"
}

echo "== 前置：清理旧实例 =="
"$START" stop >/dev/null 2>&1 || true

run_case on  "$ROOT/runtime/test-configs/crash-sentinel-m7.json" "0x0000000b"
run_case log "$ROOT/runtime/test-configs/crash-log-m7.json" "0xdeadbeef"

echo
echo "PASS：哨兵命中后 crash_mode=on 写入 waitpid 状态 0x0b；"
echo "      crash_mode=log 只记录、不改 shm。"
echo "      AFLNet 侧的 'results in a crash' 已用同一 sentinel 配置手工验证。"
