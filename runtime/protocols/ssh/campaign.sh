#!/usr/bin/env bash
# 正式 SSH campaign：tap 启动 → banner → SSH 真实登录就绪 → 0x01 快照 →
# 可选的 AFLNet -Z 启动。参数与 2026-10-29 验收一致，不做性能调优。
#
# 用法：
#   ./campaign.sh start          # 启动 QEMU 等 SSH 就绪并保存快照
#   ./campaign.sh aflnet         # 对当前实例启动 AFLNet（前台，Ctrl-C 停止）
#   ./campaign.sh status         # QEMU/forkserver/覆盖率/fuzzer_stats
#   ./campaign.sh stop           # 停止 QEMU 与 console
#
# 环境变量：
#   KIND=m7|m9                  默认 m7（C2900-UN.150.BIN）
#   TAP_IFACE=tap1               已持久化且配置 172.16.100.1/24 的 tap
#   SSH_USER/SSH_PASS=admin/cisco
#   ENABLE_PASSWORD=cisco
#   BOOT_TIMEOUT=1800  READY_TIMEOUT=300
#   AFL_OUT=... AFL_RESUME=1     输出目录非空时需显式允许续跑
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PROTO_DIR="$ROOT/runtime/protocols/ssh"
KIND="${KIND:-m7}"
TAP_IFACE="${TAP_IFACE:-tap1}"
HOST_IP="${HOST_IP:-172.16.100.1}"
GUEST_IP="${GUEST_IP:-172.16.100.2}"
NET_PORT="${NET_PORT:-22}"
SNAP_NAME="${SNAP_NAME:-snap_ssh_aflnet}"
SESSION="${C2900_FUZZ_SESSION:-c2900fuzz-ssh}"
RUNDIR="${C2900_FUZZ_RUNDIR:-$PROTO_DIR/run-aflnet}"
CONSOLE="console"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-1800}"
READY_TIMEOUT="${READY_TIMEOUT:-300}"
SSH_USER="${SSH_USER:-admin}"
SSH_PASS="${SSH_PASS:-cisco}"
ENABLE_PASSWORD="${ENABLE_PASSWORD:-cisco}"
AFL_TIMEOUT_MS="${AFL_TIMEOUT_MS:-15000}"
AFL_W="${AFL_W:-2000}"
AFL_w="${AFL_w:-100000}"
AFL_D="${AFL_D:-1500}"
SEEDS="${SEEDS:-$PROTO_DIR/seeds-aflnet}"
AFL_OUT="${AFL_OUT:-$PROTO_DIR/out-aflnet}"
AFL="${AFL_FUZZ:-$ROOT/vendor/aflnet/afl-fuzz}"
START="$ROOT/runtime/start_c2900_fuzz.sh"
CLIENT="$ROOT/runtime/fuzz_client.py"
PROBE="$ROOT/runtime/coverage_probe.py"
READY="$PROTO_DIR/ssh_ready.py"

case "$KIND" in
    m7)
        KERNEL="$ROOT/vendor/c2900/firmware/C2900-UN.150.BIN"
        FUZZ_CONFIG="$PROTO_DIR/config/fuzz.aflnet-m7.json"
        ;;
    m9)
        KERNEL="$ROOT/vendor/c2900/firmware/c2900-universalk9-mz.SPA.150-1.M9.bin"
        FUZZ_CONFIG="$PROTO_DIR/config/fuzz.aflnet-m9.json"
        ;;
    *)
        echo "KIND 必须是 m7 或 m9" >&2
        exit 2
        ;;
esac

export C2900_PKG="${C2900_PKG:-$ROOT/vendor/c2900}"
export C2900_KERNEL="$KERNEL"
export C2900_FUZZ_CONFIG="$FUZZ_CONFIG"
export C2900_FUZZ_RUNDIR="$RUNDIR"
export C2900_FUZZ_SESSION="$SESSION"
export C2900_FUZZ_CONSOLE_LOG="$RUNDIR/console.log"
export C2900_NIC_ARGS="tap,model=octeon-eth,ifname=$TAP_IFACE,script=no,downscript=no,mac=52:54:00:12:34:56"

die() { echo "错误：$*" >&2; exit 1; }
info() { echo "== $* =="; }

check_tap() {
    ip link show "$TAP_IFACE" >/dev/null 2>&1 \
        || die "找不到 $TAP_IFACE；先执行 sudo ./scripts/setup-tap.sh $TAP_IFACE"
    ip -4 addr show dev "$TAP_IFACE" | grep -q "inet $HOST_IP/" \
        || die "$TAP_IFACE 没有 $HOST_IP/24；先配置宿主 tap 地址"
}

assert_shm_ok() {
    # QEMU 的 0x2000/0x2001 段必须仍被 QEMU 映射且未标记删除；
    # 否则 AFLNet 会 attach 到另一个段，表现为 bitmap_cvg=0。
    local pid
    pid="$(pgrep -f "qemu-system-mips64.*$RUNDIR" | head -1)"
    [ -n "$pid" ] || die "QEMU 未运行（RUNDIR=$RUNDIR）"
    grep -q '/SYSV00002000' "/proc/$pid/maps" 2>/dev/null \
        || die "QEMU 没有映射 shm 0x2000；重启 campaign"
    if grep -q '/SYSV00002000 (deleted)' "/proc/$pid/maps" 2>/dev/null; then
        echo "警告：QEMU 的 shm 0x2000 带 (deleted) 标记；AFLNet 仍可按 key attach，继续" >&2
    fi
    [ -p /tmp/qemu_fuzz_ctl ] && [ -p /tmp/qemu_fuzz_st ] \
        || die "forkserver FIFO 缺失；重启 campaign"
}

console_nudge() {
    # IOS 首次 SSH 前可能仍在生成 RSA key；进入 enable 会促使它完成。
    # 只在看到明确 prompt 时发命令，避免把密码打进登录名/命令里。
    tmux has-session -t "$CONSOLE" 2>/dev/null || return 0
    info "SSH 尚未就绪，console nudge（enable/RSA）"
    local i pane
    for i in $(seq 1 30); do
        tmux send-keys -t "$CONSOLE" Enter
        sleep 1
        pane="$(tmux capture-pane -p -t "$CONSOLE" 2>/dev/null | tail -5)"
        if printf '%s\n' "$pane" | grep -q 'Password:'; then
            tmux send-keys -t "$CONSOLE" -l "$ENABLE_PASSWORD"
            sleep 0.3
            tmux send-keys -t "$CONSOLE" Enter
            sleep 3
            return 0
        fi
        if printf '%s\n' "$pane" | grep -q 'c2900#'; then
            return 0
        fi
        if printf '%s\n' "$pane" | grep -q 'c2900>'; then
            tmux send-keys -t "$CONSOLE" -l "enable"
            sleep 0.3
            tmux send-keys -t "$CONSOLE" Enter
            sleep 1
        fi
    done
}

wait_ssh_ready() {
    local deadline=$((SECONDS + READY_TIMEOUT))
    local nudged=0
    mkdir -p "$RUNDIR"
    while [ "$SECONDS" -lt "$deadline" ]; do
        if python3 "$READY" --host "$GUEST_IP" --port "$NET_PORT" \
                --user "$SSH_USER" --password "$SSH_PASS" --timeout 8 \
                >>"$RUNDIR/readiness.log" 2>&1; then
            return 0
        fi
        if [ "$nudged" -eq 0 ] && [ "$SECONDS" -gt 20 ]; then
            console_nudge
            nudged=1
        fi
        sleep 5
    done
    return 1
}

wait_no_ssh_sessions() {
    # 快照必须是“服务就绪、无活动会话”；探测脚本关闭后通常立即释放。
    tmux has-session -t "$CONSOLE" 2>/dev/null || return 0
    local i
    for i in $(seq 1 10); do
        tmux send-keys -t "$CONSOLE" -l "show ssh"
        tmux send-keys -t "$CONSOLE" Enter
        sleep 1
        if tmux capture-pane -p -t "$CONSOLE" 2>/dev/null \
                | grep -q "No SSHv2 server connections running"; then
            return 0
        fi
    done
    echo "警告：show ssh 未确认无会话；继续保存快照" >&2
}

cmd_start() {
    check_tap
    if tmux has-session -t "$CONSOLE" 2>/dev/null; then
        die "已存在 tmux 会话 '$CONSOLE'；先执行 $0 stop 或手动清理"
    fi
    "$START" stop >/dev/null 2>&1 || true
    rm -rf "$RUNDIR"
    mkdir -p "$RUNDIR"
    info "启动 QEMU（$KIND，tap=$TAP_IFACE，snapshot=$SNAP_NAME）"
    "$START" start
    "$START" wait "$BOOT_TIMEOUT"
    info "等待 SSH 真实登录就绪（最多 ${READY_TIMEOUT}s）"
    if ! wait_ssh_ready; then
        tmux capture-pane -p -S - -t "$CONSOLE" >"$RUNDIR/console-fail.txt" 2>/dev/null || true
        die "SSH 未就绪；见 $RUNDIR/readiness.log 与 console-fail.txt"
    fi
    wait_no_ssh_sessions
    sleep 2
    info "保存快照 $SNAP_NAME（0x01）"
    "$CLIENT" save --timeout 600
    assert_shm_ok
    "$PROBE" stats
    echo
    echo "快照已保存：$SNAP_NAME"
    echo "下一步：$0 aflnet"
}

cmd_aflnet() {
    [ -x "$AFL" ] || die "找不到 afl-fuzz：$AFL"
    assert_shm_ok
    [ -e /tmp/qemu_fuzz_ctl ] || die "forkserver 未就绪；先执行 $0 start"
    [ -d "$SEEDS" ] && [ -n "$(ls -A "$SEEDS" 2>/dev/null)" ] \
        || die "种子目录为空：$SEEDS"
    if [ -d "$AFL_OUT" ] && [ -n "$(ls -A "$AFL_OUT" 2>/dev/null)" ] \
            && [ "${AFL_RESUME:-0}" != "1" ]; then
        die "输出目录非空：$AFL_OUT；换 AFL_OUT 或设置 AFL_RESUME=1"
    fi
    mkdir -p "$AFL_OUT"
    echo "AFLNet: $AFL"
    echo " seeds: $SEEDS"
    echo " out:   $AFL_OUT"
    echo " target: tcp://$GUEST_IP/$NET_PORT  protocol=SSH"
    cd "$PROTO_DIR"
    exec env AFL_SKIP_CPUFREQ=1 AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 \
        "$AFL" -Z -d -i "$SEEDS" -o "$AFL_OUT" -m none \
        -t "$AFL_TIMEOUT_MS" -N "tcp://$GUEST_IP/$NET_PORT" -P SSH \
        -E -K -q 3 -s 3 -D "$AFL_D" -W "$AFL_W" -w "$AFL_w" \
        -- dummy
}

cmd_status() {
    echo "KIND=$KIND tap=$TAP_IFACE guest=$GUEST_IP:$NET_PORT snap=$SNAP_NAME"
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        echo "QEMU 会话：运行中（$SESSION）"
    else
        echo "QEMU 会话：未运行"
    fi
    if tmux has-session -t "$CONSOLE" 2>/dev/null; then
        echo "console：运行中"
    else
        echo "console：未运行"
    fi
    [ -e /tmp/qemu_fuzz_ctl ] && echo "forkserver：就绪" || echo "forkserver：缺失"
    "$PROBE" stats 2>/dev/null || true
    if [ -f "$AFL_OUT/fuzzer_stats" ]; then
        echo "--- fuzzer_stats ---"
        grep -E 'execs_done|execs_per_sec|paths_total|bitmap_cvg|stability|unique_crashes' \
            "$AFL_OUT/fuzzer_stats"
    fi
}

cmd_stop() {
    "$START" stop || true
}

usage() {
    sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

case "${1:-}" in
    start) cmd_start ;;
    aflnet) cmd_aflnet ;;
    status) cmd_status ;;
    stop) cmd_stop ;;
    *) usage ;;
esac
