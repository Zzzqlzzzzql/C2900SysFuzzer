#!/usr/bin/env bash
# 覆盖率真实性验证（不需要启动到 banner，约 20 秒）
#
# 通过 monitor 控制 CPU，做三组对照：
#   1. -S 暂停启动：bitmap 应为 0；
#   2. cont 后：edges/sum 增长，说明是 guest 真实执行写入的；
#   3. monitor stop 后：bitmap hash 连续不变；cont 后 hash 再次变化；
#   4. 0x01 快照（mid-boot）后 0x02 loadvm：bitmap 被清空/大幅缩小，
#      证明每轮 reset 与 exclude_baseline 生效。
#
# 用法：
#   ./verify-coverage.sh [m7|m9]
# 默认 m7（启动段更短，验证不依赖 banner）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KIND="${1:-m7}"
PKG="${C2900_PKG:-$ROOT/vendor/c2900}"
RUNDIR="$ROOT/runtime/run"
MON="$RUNDIR/monitor.sock"
START="$ROOT/runtime/start_c2900_fuzz.sh"
CLIENT="$ROOT/runtime/fuzz_client.py"
PROBE="$ROOT/runtime/coverage_probe.py"

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
        echo "用法：$0 [m7|m9]" >&2
        exit 2
        ;;
esac
export C2900_FUZZ_START_PAUSED=1
export C2900_FUZZ_RUNDIR="$RUNDIR"

cleanup() { "$START" stop >/dev/null 2>&1 || true; }
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
field() { "$PROBE" stats | sed -n "s/.*$1=\([^ ]*\).*/\1/p"; }
mon_cmd() { printf '%s\n' "$1" | timeout 5 socat - UNIX-CONNECT:"$MON" >/dev/null 2>&1 || true; }

echo "== 清理旧实例 =="
"$START" stop >/dev/null 2>&1 || true

echo "== 以暂停状态启动（-S，$KIND） =="
"$START" start
"$PROBE" wait 60 >/dev/null || fail "shm 未就绪"

e0="$(field edges)"; s0="$(field sum)"
echo "  paused: edges=$e0 sum=$s0"
[ "$e0" -eq 0 ] && [ "$s0" -eq 0 ] || fail "暂停状态下 bitmap 非空，覆盖率来源可疑"

echo "== cont：guest 开始执行 =="
mon_cmd cont
sleep 3
e1="$(field edges)"; s1="$(field sum)"; h1="$(field hash)"
echo "  running: edges=$e1 sum=$s1 hash=$h1"
[ "$e1" -gt 0 ] || fail "cont 后 bitmap 仍为空"
[ "$s1" -gt 0 ] || fail "cont 后命中计数之和仍为 0"

echo "== stop：冻结后 bitmap 必须不变 =="
mon_cmd stop
sleep 1
e2="$(field edges)"; s2="$(field sum)"; h2="$(field hash)"
sleep 2
e3="$(field edges)"; s3="$(field sum)"; h3="$(field hash)"
echo "  stopped: edges=$e2/$e3 sum=$s2/$s3 hash=$h2/$h3"
[ "$h2" = "$h3" ] || fail "VM 已 stop，bitmap 仍在变化"
[ "$s2" = "$s3" ] || fail "VM 已 stop，命中计数仍在变化"

echo "== cont：bitmap 必须再次变化 =="
mon_cmd cont
sleep 2
e4="$(field edges)"; s4="$(field sum)"; h4="$(field hash)"
echo "  resumed: edges=$e4 sum=$s4 hash=$h4"
[ "$h4" != "$h3" ] || fail "cont 后 bitmap 未变化"

echo "== 快照 + loadvm：每轮 reset 必须清空/大幅缩小 bitmap =="
e5_before="$e4"
"$CLIENT" save >/dev/null || fail "0x01 save 失败"
e5="$(field edges)"; h5="$(field hash)"
echo "  snapshot(mid-boot): edges=$e5 hash=$h5"
"$CLIENT" round >/dev/null || fail "0x02 loadvm 失败"
e6="$(field edges)"; h6="$(field hash)"
echo "  after 0x02: edges=$e6 hash=$h6"
"$CLIENT" end >/dev/null || fail "0x03 失败"
e7="$(field edges)"; h7="$(field hash)"
echo "  after 0x03: edges=$e7 hash=$h7"
[ "$e6" -lt "$e5" ] || fail "0x02 后 bitmap 未缩小（reset/exclude_baseline 未生效）"
[ "$e6" -lt 20000 ] || fail "0x02 后 bitmap 仍然过大，疑似 boot baseline 未排除"

echo
echo "PASS：覆盖率是 guest 执行实时写入的；VM 停止时冻结；每轮 loadvm 后重置。"
echo "  paused 0 / running $e1 / stopped $e3 / resumed $e4 / snapshot $e5 / round $e6 / end $e7"
