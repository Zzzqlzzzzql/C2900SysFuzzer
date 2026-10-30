#!/usr/bin/env bash
# 验证 AFLNet 确实能驱动本项目 QEMU forkserver 并使用 0x2000 bitmap。
#
# 重要：本测试使用一个宿主侧 dummy HTTP 服务器作为 AFLNet 的网络对端，
# 不是 C2900 的真实 HTTP 服务。它验证的是：
#   - AFLNet 的 -Z 初始化握手（0x01 保存快照）；
#   - 每轮 0x02/0x03、网络收发、shm 0x2000 读取；
#   - AFLNet fuzzer_stats 中的 bitmap_cvg/stability 与 QEMU bitmap 对得上。
# 它不验证 C2900 服务的协议解析或漏洞。
#
# 用法：
#   ./verify-aflnet.sh [seconds]     # 默认 25 秒（AFLNet 启动/统计需要足够轮次）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SECS="${1:-25}"
PKG="${C2900_PKG:-$ROOT/vendor/c2900}"
AFL="${AFL_FUZZ:-$ROOT/vendor/aflnet/afl-fuzz}"
START="$ROOT/runtime/start_c2900_fuzz.sh"
PROBE="$ROOT/runtime/coverage_probe.py"
PORT="${AFLNET_TEST_PORT:-19080}"
WORK="$ROOT/runtime/run/aflnet-verify"
MON="$ROOT/runtime/run/monitor.sock"

fail() { echo "FAIL: $*" >&2; exit 1; }
mon_cmd() { printf '%s\n' "$1" | timeout 5 socat - UNIX-CONNECT:"$MON" >/dev/null 2>&1 || true; }

[ -x "$AFL" ] || fail "找不到 afl-fuzz：$AFL"
command -v socat >/dev/null || fail "需要 socat"

rm -rf "$WORK"
mkdir -p "$WORK/seeds"

# 用于验证 FIFO/shm 链路的最小 HTTP 种子
printf 'GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n' > "$WORK/seeds/req"

export C2900_FUZZ_START_PAUSED=1
export C2900_KERNEL="${C2900_KERNEL:-$PKG/firmware/C2900-UN.150.BIN}"
export C2900_FUZZ_CONFIG="${C2900_FUZZ_CONFIG:-$ROOT/runtime/c2900-ios-m7.json}"

cleanup() {
    pkill -f "dummy_http_server.py $PORT" 2>/dev/null || true
    "$START" stop >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "== 启动 QEMU（暂停，M7） =="
"$START" stop >/dev/null 2>&1 || true
"$START" start >/dev/null
"$PROBE" wait 60 >/dev/null || fail "QEMU shm 未就绪"

echo "== 启动 dummy HTTP 服务器（127.0.0.1:$PORT） =="
python3 "$ROOT/scripts/dummy_http_server.py" "$PORT" > "$WORK/server.log" 2>&1 &
SERVER_PID=$!
sleep 1
kill -0 "$SERVER_PID" 2>/dev/null || fail "dummy server 启动失败"

echo "== 运行 AFLNet ${SECS}s =="
cd "$WORK"
set +e
timeout -s INT "$SECS" env \
    AFL_NO_UI=1 AFL_SKIP_CPUFREQ=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 \
    "$AFL" -Z -d -i seeds -o out -m none -t 8000 \
    -N "tcp://127.0.0.1/$PORT" -P HTTP -E -K -q 3 -s 3 -D 1000 -W 100 \
    -- dummy > afl.log 2>&1
AFL_RC=$?
set -e
# timeout 以 124 退出是预期（我们主动结束）；AFLNet 自己 abort 才是失败
if [ "$AFL_RC" -ne 0 ] && [ "$AFL_RC" -ne 124 ]; then
    echo "---- afl.log ----" >&2
    tail -40 afl.log >&2
    fail "afl-fuzz 异常退出（rc=$AFL_RC）"
fi

STATS="$WORK/out/fuzzer_stats"
[ -f "$STATS" ] || { tail -40 "$WORK/afl.log" >&2; fail "没有生成 fuzzer_stats"; }

execs="$(awk '$1 == "execs_done" { print $3 }' "$STATS")"
cvg="$(awk '$1 == "bitmap_cvg" { print $3 }' "$STATS" | tr -d '%')"
stab="$(awk '$1 == "stability" { print $3 }' "$STATS" | tr -d '%')"
paths="$(awk '$1 == "paths_total" { print $3 }' "$STATS")"

echo "  AFLNet: execs=$execs bitmap_cvg=${cvg}% stability=${stab}% paths=$paths"

[ "${execs:-0}" -gt 0 ] || fail "AFLNet 没有执行任何一轮"
[ -n "$cvg" ] || fail "AFLNet 没有 bitmap_cvg"
awk -v v="$cvg" 'BEGIN { exit !(v > 0) }' || fail "AFLNet bitmap_cvg=0，未读到覆盖率"
awk -v v="$stab" 'BEGIN { exit !(v > 99.0) }' || fail "stability 不是 100%，快照重置/覆盖率可能不稳定"

# 暂停 QEMU 后再读 shm，避免 guest 继续执行导致数字漂移
mon_cmd stop
sleep 1
qemu_line="$("$PROBE" stats)"
qemu_edges="$(printf '%s\n' "$qemu_line" | sed -n 's/.*edges=\([0-9]*\).*/\1/p')"
qemu_hash="$(printf '%s\n' "$qemu_line" | sed -n 's/.*hash=\([0-9a-f]*\).*/\1/p')"
expected="$(awk -v c="$cvg" 'BEGIN { printf "%.0f", c * 65536 / 100 }')"
echo "  QEMU:   edges=$qemu_edges hash=$qemu_hash"
echo "  AFLNet bitmap_cvg ${cvg}% ≈ ${expected} edges；QEMU shm edges=$qemu_edges"

# 允许 25% 的偏差：AFLNet 的数字来自最后一轮后的统计时刻，QEMU 的 shm 还包含
# 之后到 stop 之间执行到的少量新边。
awk -v a="$qemu_edges" -v b="$expected" 'BEGIN {
    d = (a > b) ? (a - b) : (b - a);
    if (d > (a * 0.25 + 100)) exit 1;
}' || fail "AFLNet bitmap_cvg 与 QEMU shm 边数差距过大（协议/bitmap 不匹配？）"

echo
echo "PASS：AFLNet 成功连接 QEMU forkserver、执行 $execs 轮，并读取到非零、"
echo "      稳定的覆盖率（bitmap_cvg=${cvg}%，stability=${stab}%）。"
echo "      注意：本轮网络对端是 dummy HTTP 服务器，不是 C2900 服务。"
echo "      产物：$WORK（afl.log、out/fuzzer_stats）"
