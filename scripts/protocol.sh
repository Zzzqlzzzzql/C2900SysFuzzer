#!/usr/bin/env bash
# 按协议运行 C2900 fuzz 准备流程。
#
# 每个协议有独立目录：runtime/protocols/<proto>/
#   protocol.env        协议配置（配置脚本、快照名、端口、AFLNet 参数）
#   config/fuzz.json    QEMU -fuzz 配置（含 snapshot_name）
#   logs/               console/configure/snapshot/coverage 日志
#   snapshots/          快照元数据（时间、配置脚本、固件/配置 hash、覆盖率）
#   seeds/              AFLNet 种子
#   out/                AFLNet 输出
#
# 用法：
#   ./protocol.sh list
#   ./protocol.sh prepare <proto>   启动→banner→跑 C2900 配置脚本→自动 0x01 快照
#   ./protocol.sh configure <proto> 对已运行的实例重跑配置→0x01 快照（调试用）
#   ./protocol.sh status <proto>
#   ./protocol.sh aflnet-cmd <proto>
#   ./protocol.sh stop <proto>
#
# prepare 结束后 QEMU 保持运行，快照保存在 QEMU 的临时 overlay 中；
# 直接在该状态上启动 AFLNet（AFLNet 启动时会再发一次 0x01）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_BASE="$ROOT/runtime/protocols"
C2900_PKG="${C2900_PKG:-$ROOT/vendor/c2900}"
START="$ROOT/runtime/start_c2900_fuzz.sh"
CLIENT="$ROOT/runtime/fuzz_client.py"
PROBE="$ROOT/runtime/coverage_probe.py"
AFL="${AFL_FUZZ:-$ROOT/vendor/aflnet/afl-fuzz}"

die() { echo "错误：$*" >&2; exit 1; }

capture_console() {
    local proto_dir="$1"
    tmux capture-pane -p -S - -t "console:0.0" \
        > "$proto_dir/logs/console-final.txt" 2>/dev/null || true
}

archive_snapshot_meta() {
    local f="$PROTO_DIR/snapshots/$SNAPSHOT_NAME.json"
    if [ -f "$f" ]; then
        mkdir -p "$PROTO_DIR/snapshots/history"
        mv "$f" "$PROTO_DIR/snapshots/history/$SNAPSHOT_NAME.$(date +%Y%m%d-%H%M%S).json"
    fi
}

usage() {
    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

resolve() {
    local proto="${1:-}"
    [ -n "$proto" ] || usage
    PROTO_DIR="$PROTO_BASE/$proto"
    [ -d "$PROTO_DIR" ] || die "未知协议 '$proto'（见 $0 list）"
    [ -f "$PROTO_DIR/protocol.env" ] || die "缺少 $PROTO_DIR/protocol.env"
    # shellcheck disable=SC1090
    source "$PROTO_DIR/protocol.env"

    if [[ "$CONFIG_SCRIPT_REL" = /* ]]; then
        CONFIG_SCRIPT="$CONFIG_SCRIPT_REL"
    elif [ -f "$ROOT/$CONFIG_SCRIPT_REL" ]; then
        CONFIG_SCRIPT="$ROOT/$CONFIG_SCRIPT_REL"
    else
        CONFIG_SCRIPT="$C2900_PKG/test/$CONFIG_SCRIPT_REL"
    fi
    KERNEL="${C2900_KERNEL:-$C2900_PKG/${KERNEL_REL:-firmware/c2900-universalk9-mz.SPA.150-1.M9.bin}}"
    FUZZ_CONFIG="${C2900_FUZZ_CONFIG:-$PROTO_DIR/config/fuzz.json}"

    export C2900_PKG
    export C2900_KERNEL="$KERNEL"
    export C2900_FUZZ_CONFIG="$FUZZ_CONFIG"
    export C2900_FUZZ_RUNDIR="$PROTO_DIR"
    export C2900_FUZZ_CONSOLE_LOG="$PROTO_DIR/logs/console.log"
    export C2900_FUZZ_SESSION="c2900fuzz-$PROTO_NAME"
    export C2900_CONFIG_PROMPT_TIMEOUT="${C2900_CONFIG_PROMPT_TIMEOUT:-300}"
    export C2900_CONFIG_TEXT_TIMEOUT="${C2900_CONFIG_TEXT_TIMEOUT:-600}"
    export C2900_CONFIG_RSA_TIMEOUT="${C2900_CONFIG_RSA_TIMEOUT:-900}"

    # 网络后端：socket（默认，仅 boot/覆盖率，AFLNet 无法直接到 guest）/
    # tap（需宿主 sudo 建 tap 并配 172.16.100.1/24）/ user（需 QEMU 编入 libslirp）。
    case "${NET_MODE:-socket}" in
        tap)
            export C2900_NIC_ARGS="tap,model=${C2900_NIC_MODEL:-octeon-eth},ifname=${TAP_IFACE:-tap1},script=no,downscript=no,mac=52:54:00:12:34:56"
            ;;
        user)
            export C2900_NIC_ARGS="user,model=${C2900_NIC_MODEL:-octeon-eth},hostfwd=tcp:127.0.0.1:${HOSTFWD_PORT:-$((20000 + NET_PORT))}-:${NET_PORT},mac=52:54:00:12:34:56"
            ;;
        socket|"")
            unset C2900_NIC_ARGS || true
            ;;
        *)
            die "未知 NET_MODE=$NET_MODE（socket/tap/user）"
            ;;
    esac

    [ -f "$CONFIG_SCRIPT" ] || die "配置脚本不存在：$CONFIG_SCRIPT"
    [ -f "$FUZZ_CONFIG" ] || die "fuzz 配置不存在：$FUZZ_CONFIG"
    mkdir -p "$PROTO_DIR/logs" "$PROTO_DIR/snapshots" "$PROTO_DIR/seeds" "$PROTO_DIR/out"
}

cmd_list() {
    printf '%-10s %-10s %-18s %-8s %s\n' "PROTO" "AFLNET" "SNAPSHOT" "PORT" "CONFIG"
    for d in "$PROTO_BASE"/*/; do
        [ -f "$d/protocol.env" ] || continue
        (
            # shellcheck disable=SC1090
            source "$d/protocol.env"
            snap=no
            [ -f "$d/snapshots/$SNAPSHOT_NAME.json" ] && snap=yes
            printf '%-10s %-10s %-18s %-8s %s\n' \
                "$PROTO_NAME" "$AFLNET_PROTOCOL" "$SNAPSHOT_NAME($snap)" \
                "$NET_PORT" "$CONFIG_SCRIPT_REL"
        )
    done
}

cmd_prepare() {
    local proto="$1"
    resolve "$proto"
    echo "== 协议：$DISPLAY_NAME（$PROTO_NAME） =="
    echo "  配置脚本：$CONFIG_SCRIPT"
    echo "  快照名：$SNAPSHOT_NAME"
    echo "  运行目录：$PROTO_DIR"

    # 同一时间只允许一个 fuzz 实例；console 会话名是 vendor 脚本硬编码的。
    local running
    running="$(tmux ls -F '#{session_name}' 2>/dev/null | grep -E '^c2900fuzz-' || true)"
    [ -z "$running" ] || die "已有 fuzz 实例在运行：$running；先执行 stop"
    if tmux has-session -t console 2>/dev/null; then
        die "已存在 tmux 会话 'console'；请先停止原有 C2900 console"
    fi

    : > "$PROTO_DIR/logs/configure.log"
    : > "$PROTO_DIR/logs/snapshot.log"
    : > "$PROTO_DIR/logs/console.log"

    "$START" stop >/dev/null 2>&1 || true
    echo "== 启动 QEMU 并等待 banner（超时 ${BOOT_TIMEOUT}s） =="
    "$START" start
    "$START" wait "$BOOT_TIMEOUT"

    do_configure
}

do_configure() {
    local meta="$PROTO_DIR/snapshots/$SNAPSHOT_NAME.json"
    local prev_kernel_source=""
    if [ -f "$meta" ]; then
        prev_kernel_source="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("kernel_source",""))' "$meta" 2>/dev/null || true)"
    fi
    archive_snapshot_meta
    echo "== 运行 C2900 配置脚本 =="
    set +e
    timeout "$CONFIG_TIMEOUT" python3 "$CONFIG_SCRIPT" 2>&1 \
        | tee -a "$PROTO_DIR/logs/configure.log"
    local cfg_rc=${PIPESTATUS[0]}
    set -e
    capture_console "$PROTO_DIR"
    if [ "$cfg_rc" -ne 0 ]; then
        echo "错误：配置脚本失败（rc=$cfg_rc），QEMU 保留运行以便排查。" >&2
        echo "  日志：$PROTO_DIR/logs/configure.log" >&2
        echo "  控制台快照：$PROTO_DIR/logs/console-final.txt" >&2
        echo "  停止：$0 stop $PROTO_NAME" >&2
        exit 1
    fi

    echo "== 配置完成，发送 0x01 保存快照 '$SNAPSHOT_NAME' =="
    set +e
    "$CLIENT" save --timeout 600 2>&1 | tee -a "$PROTO_DIR/logs/snapshot.log"
    local snap_rc=${PIPESTATUS[0]}
    set -e
    [ "$snap_rc" -eq 0 ] || die "快照保存失败（见 $PROTO_DIR/logs/snapshot.log）"

    "$PROBE" stats | tee "$PROTO_DIR/logs/coverage-at-snapshot.txt" >/dev/null

    # 以运行中的 run_qemu.sh 为准，避免 configure 复用实例时把 M7 误记成 M9
    local kernel_used="$KERNEL"
    local running_kernel=""
    if [ -f "$PROTO_DIR/run_qemu.sh" ]; then
        running_kernel="$(python3 -c 'import re,sys; s=open(sys.argv[1]).read(); m=re.search(r"-kernel \"([^\"]+)\"", s); print(m.group(1) if m else "")' "$PROTO_DIR/run_qemu.sh" 2>/dev/null || true)"
    fi
    [ -n "$running_kernel" ] && kernel_used="$running_kernel"
    local kernel_source="$KERNEL"
    if [ -n "${C2900_KERNEL:-}" ]; then
        kernel_source="$C2900_KERNEL"
    elif [ -n "$prev_kernel_source" ]; then
        kernel_source="$prev_kernel_source"
    fi
    export PROTO_NAME DISPLAY_NAME SNAPSHOT_NAME CONFIG_SCRIPT NOTES
    export NET_TRANSPORT NET_PORT GUEST_IP NET_MODE AFLNET_PROTOCOL
    export KERNEL C2900_FUZZ_SESSION C2900_FUZZ_CONFIG C2900_FUZZ_RUNDIR
    export CONFIG_RC="$cfg_rc" KERNEL_USED="$kernel_used" KERNEL_SOURCE="$kernel_source"
    python3 - "$PROTO_DIR/snapshots/$SNAPSHOT_NAME.json" <<'PY'
import hashlib, json, os, sys, time
out = sys.argv[1]
env = os.environ
def sha(p):
    h = hashlib.sha256()
    try:
        with open(p, 'rb') as f:
            for b in iter(lambda: f.read(1 << 20), b''):
                h.update(b)
        return h.hexdigest()
    except OSError:
        return None
proto_dir = env['C2900_FUZZ_RUNDIR']
coverage_line = ''
try:
    coverage_line = open(os.path.join(proto_dir, 'logs', 'coverage-at-snapshot.txt')).read().strip()
except OSError:
    pass
meta = {
    'protocol': env['PROTO_NAME'],
    'display_name': env['DISPLAY_NAME'],
    'snapshot_name': env['SNAPSHOT_NAME'],
    'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    'config_script': env['CONFIG_SCRIPT'],
    'config_exit': int(os.environ.get('CONFIG_RC', '0')),
    'snapshot_status': 1,
    'kernel_source': env['KERNEL_SOURCE'],
    'kernel_used': env['KERNEL_USED'],
    'kernel_sha256': sha(env['KERNEL_USED']),
    'fuzz_config': env['C2900_FUZZ_CONFIG'],
    'fuzz_config_sha256': sha(env['C2900_FUZZ_CONFIG']),
    'qemu_session': env['C2900_FUZZ_SESSION'],
    'coverage_at_snapshot': coverage_line,
    'aflnet_protocol': env.get('AFLNET_PROTOCOL'),
    'network': f"{env.get('NET_TRANSPORT')}://{env.get('GUEST_IP')}:{env.get('NET_PORT')}",
    'net_mode': env.get('NET_MODE'),
    'notes': env.get('NOTES'),
    'logs': {
        'console': os.path.join(proto_dir, 'logs', 'console.log'),
        'configure': os.path.join(proto_dir, 'logs', 'configure.log'),
        'snapshot': os.path.join(proto_dir, 'logs', 'snapshot.log'),
        'coverage': os.path.join(proto_dir, 'Logs', 'coverage.log'),
        'forkserver': os.path.join(proto_dir, 'Logs', 'forkserver.log'),
        'serial': os.path.join(proto_dir, 'serial.log'),
    },
}
with open(out, 'w') as f:
    json.dump(meta, f, indent=2)
print(f"  快照元数据：{out}")
PY
}

cmd_configure() {
    local proto="$1"
    resolve "$proto"
    tmux has-session -t "$C2900_FUZZ_SESSION" 2>/dev/null \
        || die "QEMU 会话 $C2900_FUZZ_SESSION 未运行；先 prepare 或 start"
    do_configure
}


cmd_status() {
    local proto="$1"
    resolve "$proto"
    echo "== $DISPLAY_NAME（$PROTO_NAME） =="
    if tmux has-session -t "$C2900_FUZZ_SESSION" 2>/dev/null; then
        echo "QEMU 会话：运行中（$C2900_FUZZ_SESSION）"
    else
        echo "QEMU 会话：未运行"
    fi
    if tmux has-session -t console 2>/dev/null; then
        echo "console 会话：运行中"
    else
        echo "console 会话：未运行"
    fi
    if [ -f "$PROTO_DIR/snapshots/$SNAPSHOT_NAME.json" ]; then
        echo "快照元数据：$PROTO_DIR/snapshots/$SNAPSHOT_NAME.json"
        python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print("  时间：",d["created_at"]); print("  配置：",d["config_script"]); print("  覆盖：",d["coverage_at_snapshot"])' \
            "$PROTO_DIR/snapshots/$SNAPSHOT_NAME.json"
    else
        echo "快照元数据：无（尚未 prepare）"
    fi
    if [ -f /tmp/qemu_fuzz_ctl ] && [ -f "$PROTO_DIR/monitor.sock" ]; then
        echo -n "当前 shm："; "$PROBE" stats || true
    fi
    echo "日志目录：$PROTO_DIR/logs"
}

cmd_aflnet_cmd() {
    local proto="$1"
    resolve "$proto"
    [ -x "$AFL" ] || die "找不到 afl-fuzz：$AFL"
    case "$AFLNET_PROTOCOL" in
        ""|NONE|CUSTOM)
            die "$DISPLAY_NAME 没有 stock AFLNet -P（$AFLNET_PROTOCOL）；需要自定义 harness。$NOTES"
            ;;
    esac
    echo "# 注意：当前 NET_MODE=$NET_MODE。"
    if [ "$NET_MODE" = "socket" ]; then
        echo "# QEMU 使用 -nic socket，宿主没有到 guest 的 TCP/UDP 路径；"
        echo "# 需安装 libslirp 用 -nic user，或切 tap 后再执行下面命令。"
    fi
    cat <<EOF
(cd "$PROTO_DIR" && "$AFL" -Z -d -i seeds -o out -m none -t 10000 \\
  -N "$NET_TRANSPORT://$GUEST_IP/$NET_PORT" -P "$AFLNET_PROTOCOL" \\
  -E -K -q 3 -s 3 -D 1500 -W 500 -- dummy)
EOF
}

main() {
    local cmd="${1:-}"
    case "$cmd" in
        list) cmd_list ;;
        prepare) [ $# -eq 2 ] || usage; cmd_prepare "$2" ;;
        configure) [ $# -eq 2 ] || usage; cmd_configure "$2" ;;
        status) [ $# -eq 2 ] || usage; cmd_status "$2" ;;
        aflnet-cmd) [ $# -eq 2 ] || usage; cmd_aflnet_cmd "$2" ;;
        stop)
            [ $# -eq 2 ] || usage
            resolve "$2"
            "$START" stop
            ;;
        *) usage ;;
    esac
}

main "$@"
