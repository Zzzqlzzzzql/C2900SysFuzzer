#!/usr/bin/env bash
# C2900 系统级 fuzz 原型启动脚本
#
#   使用改进版 QEMU（qemu-8.2-eval-v3 + fuzz 框架）启动 C2900 IOS，
#   启用 -fuzz 快照型 forkserver 与 phys 归一化覆盖率采集。
#
# 用法：
#   ./start_c2900_fuzz.sh          # 启动（tmux 会话 c2900fuzz）
#   ./start_c2900_fuzz.sh stop     # 停止
#   ./start_c2900_fuzz.sh status   # 查看状态
#   ./start_c2900_fuzz.sh wait     # 等待 banner（默认 240s）
#
# 说明：
#   - 串口输出写入 $RUNDIR/serial.log（file chardev，无控制台输入）；
#   - 使用 -snapshot，guest 对 flash/disk 的写入落在临时 overlay，不改固件；
#   - 默认使用 -nic socket（宿主构建无 libslirp，不能 -nic user）；
#   - forkserver IPC：/tmp/qemu_fuzz_ctl、/tmp/qemu_fuzz_st，
#     共享内存 key 0x2000（edge bitmap）/ 0x2001（exit status）；
#   - 覆盖率日志：$RUNDIR/Logs/coverage.log；
#   - 协议：0x01 保存快照，0x02 loadvm+重置覆盖率，0x03 本轮结束。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# C2900 仿真包（固件/磁盘镜像）位置：默认使用项目内 vendor/ 副本。
if [ -n "${C2900_PKG:-}" ]; then
    :
elif [ -d "$PROJECT_ROOT/vendor/c2900/firmware" ]; then
    C2900_PKG="$PROJECT_ROOT/vendor/c2900"
else
    echo "错误：找不到 vendor/c2900；请先完成资产复制或设置 C2900_PKG" >&2
    exit 1
fi
RUNDIR="${C2900_FUZZ_RUNDIR:-$SCRIPT_DIR/run}"
CFG="${C2900_FUZZ_CONFIG:-$SCRIPT_DIR/c2900-ios.json}"
QEMU="${C2900_QEMU:-$PROJECT_ROOT/vendor/qemu/bin/qemu-system-mips64}"
QEMU_LIB="${C2900_QEMU_LIB:-$PROJECT_ROOT/vendor/qemu/lib}"
C2900_LIB="${C2900_LIB_DIR:-$C2900_PKG/lib}"
MACH="${C2900_MACHINE:-octeon}"
NICMODEL="${C2900_NIC_MODEL:-octeon-eth}"
CONSOLE_PORT="${C2900_CONSOLE_PORT:-5554}"
# vendor 配置脚本固定使用 console；build.sh smoke test 可用环境变量隔离
CONSOLE_SESSION="${C2900_CONSOLE_SESSION:-console}"
# 默认用 M9 外层镜像（stub 自行解压）；也可指向 C2900-UN.150.BIN（M7 解压镜像）
KERNEL="${C2900_KERNEL:-$C2900_PKG/firmware/c2900-universalk9-mz.SPA.150-1.M9.bin}"
SESSION="${C2900_FUZZ_SESSION:-c2900fuzz}"
NET_PORT="${C2900_NET_PORT:-45678}"
# 网络后端可通过 C2900_NIC_ARGS 覆盖（例如 tap/user）；默认 socket。
NIC_ARGS="${C2900_NIC_ARGS:-socket,model=$NICMODEL,listen=127.0.0.1:$NET_PORT,mac=52:54:00:12:34:56}"
# 调试/验证用：C2900_FUZZ_START_PAUSED=1 时加 -S，CPU 暂停启动。
PAUSE_ARG=""
if [ "${C2900_FUZZ_START_PAUSED:-0}" = "1" ]; then
    PAUSE_ARG="-S"
fi

stop_qemu() {
    local pane_pid=""
    if tmux has-session -t "$SESSION" 2>/dev/null; then
        pane_pid="$(tmux list-panes -t "$SESSION" -F '#{pane_pid}' 2>/dev/null | head -1)"
        tmux kill-session -t "$SESSION" 2>/dev/null || true
        # 等 QEMU 真正退出（atexit 会删 shm/FIFO），避免旧实例清理到新实例
        if [ -n "$pane_pid" ]; then
            for _ in $(seq 1 50); do
                kill -0 "$pane_pid" 2>/dev/null || break
                sleep 0.1
            done
            kill -9 "$pane_pid" 2>/dev/null || true
        fi
        echo "已停止 tmux 会话 '$SESSION'"
    else
        echo "未在运行（无 tmux 会话 '$SESSION'）"
    fi
    # 只清理本脚本启动的 console 会话（vendor 配置脚本使用固定名字 console）
    if [ -f "$RUNDIR/console.owned" ]; then
        tmux kill-session -t "$CONSOLE_SESSION" 2>/dev/null || true
        rm -f "$RUNDIR/console.owned"
    fi
    rm -f /tmp/qemu_fuzz_ctl /tmp/qemu_fuzz_st /tmp/qemu_fuzz_auto
    rm -f "$RUNDIR/monitor.sock" "$RUNDIR/kernel-elffix.bin"
    # QEMU 被 SIGTERM/SIGHUP 终止时 atexit 不一定执行，清掉本实例的 shm
    for k in 0x2000 0x2001; do
        id="$(ipcs -m | awk -v k="$k" '$1 == k { print $2; exit }')"
        [ -n "$id" ] && ipcrm -m "$id" 2>/dev/null || true
    done
}

case "${1:-start}" in
    stop)
        stop_qemu
        exit 0
        ;;
    status)
        if tmux has-session -t "$SESSION" 2>/dev/null; then
            echo "运行中：tmux session=$SESSION"
            echo "串口日志：$RUNDIR/serial.log"
            echo "覆盖率日志：$RUNDIR/Logs/coverage.log"
            [ -f "$RUNDIR/Logs/coverage.log" ] && tail -3 "$RUNDIR/Logs/coverage.log"
            [ -f /tmp/qemu_fuzz_ctl ] && echo "forkserver IPC：已创建"
        else
            echo "未运行"
            exit 1
        fi
        exit 0
        ;;
    wait)
        timeout="${2:-240}"
        echo "等待 C2900 banner（最多 ${timeout}s）..."
        for _ in $(seq 1 "$timeout"); do
            if grep -q "Press RETURN to get started" "$RUNDIR/serial.log" 2>/dev/null; then
                echo "已到达 IOS banner"
                exit 0
            fi
            if ! tmux has-session -t "$SESSION" 2>/dev/null; then
                echo "QEMU 已退出，检查 $RUNDIR/qemu.log" >&2
                exit 1
            fi
            sleep 1
        done
        echo "等待超时，当前串口尾部：" >&2
        tail -20 "$RUNDIR/serial.log" 2>/dev/null >&2 || true
        exit 1
        ;;
    start)
        ;;
    *)
        echo "用法：$0 [start|stop|status|wait [seconds]]" >&2
        exit 2
        ;;
esac

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "已在运行（tmux session=$SESSION）；先执行 $0 stop" >&2
    exit 1
fi

mkdir -p "$RUNDIR/Logs"
rm -f /tmp/qemu_fuzz_ctl /tmp/qemu_fuzz_st /tmp/qemu_fuzz_auto
rm -f "$RUNDIR/serial.log" "$RUNDIR/qemu.log"

# M9 外层镜像的 ELF e_machine 是 Cisco 自定义的 0xC2，QEMU loader 会拒绝。
# 厂商 qemu_wrapper.sh 的做法是把 offset 18 的 e_machine 改成 0x0008；
# 这里对副本做同样处理，不改动 firmware/ 原文件。
if [ -f "$KERNEL" ]; then
    em="$(od -An -tx1 -j18 -N2 "$KERNEL" | tr -d ' \n')"
    if [ "$em" = "00c2" ] || [ "$em" = "c200" ]; then
        cp -f "$KERNEL" "$RUNDIR/kernel-elffix.bin"
        printf '\x00\x08' | dd of="$RUNDIR/kernel-elffix.bin" bs=1 seek=18 \
            conv=notrunc status=none
        echo "已为 M9 外层镜像生成 loader 兼容副本：$RUNDIR/kernel-elffix.bin"
        KERNEL="$RUNDIR/kernel-elffix.bin"
    fi
fi

export LD_LIBRARY_PATH="$QEMU_LIB:$C2900_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export C2900_CONSOLE_PORT="$CONSOLE_PORT"

# tmux 会话内直接重定向 QEMU 的输出；命令行通过独立脚本落地，避免引号问题
cat > "$RUNDIR/run_qemu.sh" <<EOF
#!/usr/bin/env bash
exec env LD_LIBRARY_PATH="$LD_LIBRARY_PATH" "$QEMU" \\
    -M "$MACH" -cpu Octeon52XX -m 512 \\
    -kernel "$KERNEL" \\
    -bios "$C2900_PKG/firmware/mips64_mixed.bin" \\
    -drive "file=$C2900_PKG/firmware/rommon_var.bin,format=raw,id=iomem" \\
    -drive "if=pflash,unit=1,format=raw,file=$C2900_PKG/firmware/flash.bin" \\
    -drive "file=$C2900_PKG/firmware/disk0.raw,id=disk0,format=raw" \\
    -display none \\
    -chardev "file,id=char0,path=$RUNDIR/serial.log,mux=on" \\
    -serial chardev:char0 \\
    -chardev "socket,id=char1,host=127.0.0.1,port=$CONSOLE_PORT,server=on,wait=off" \\
    -serial chardev:char1 \\
    -monitor "unix:$RUNDIR/monitor.sock,server=on,wait=off" \\
    -icount shift=1,sleep=off \\
    -nic "$NIC_ARGS" \\
    -snapshot $PAUSE_ARG \\
    -fuzz "$CFG" \\
    > "$RUNDIR/qemu.log" 2>&1
EOF
chmod +x "$RUNDIR/run_qemu.sh"
tmux new-session -d -s "$SESSION" -c "$RUNDIR" "$RUNDIR/run_qemu.sh"

sleep 2
if tmux has-session -t "$SESSION" 2>/dev/null; then
    # 启动 IOS 控制台（vendor 配置脚本固定使用 tmux 会话名 console）
    if tmux has-session -t "$CONSOLE_SESSION" 2>/dev/null; then
        echo "错误：tmux 会话 '$CONSOLE_SESSION' 已存在；请先停止原有 C2900 console" >&2
        tmux kill-session -t "$SESSION" 2>/dev/null || true
        exit 1
    fi
    CONSOLE_LOG="${C2900_FUZZ_CONSOLE_LOG:-$RUNDIR/console.log}"
    if ! tmux new-session -d -s "$CONSOLE_SESSION" -c "$RUNDIR" \
            env C2900_CONSOLE_PORT="$CONSOLE_PORT" \
            python3 "$C2900_PKG/test/connect_console.py" 2>"$RUNDIR/console.err"; then
        echo "错误：无法启动 console 会话，见 $RUNDIR/console.err" >&2
        tmux kill-session -t "$SESSION" 2>/dev/null || true
        exit 1
    fi
    touch "$RUNDIR/console.owned"
    tmux pipe-pane -t "$CONSOLE_SESSION:0.0" -o "cat >> '$CONSOLE_LOG'" 2>/dev/null || true

    echo "C2900 fuzz QEMU 已启动（tmux session=$SESSION）"
    echo "  固件：$KERNEL"
    echo "  配置：$CFG"
    echo "  串口：$RUNDIR/serial.log"
    echo "  控制台：tmux session=$CONSOLE_SESSION（serial 127.0.0.1:$CONSOLE_PORT，日志 $CONSOLE_LOG）"
    echo "  覆盖率：$RUNDIR/Logs/coverage.log"
    echo "  监视器：$RUNDIR/monitor.sock（socat - UNIX-CONNECT:$RUNDIR/monitor.sock）"
    echo "  forkserver：/tmp/qemu_fuzz_ctl、/tmp/qemu_fuzz_st"
    echo "  等待 banner：$0 wait"
    [ -n "$PAUSE_ARG" ] && echo "  注意：CPU 以暂停状态启动（-S），用 monitor 的 cont 恢复"
    echo "  停止：$0 stop"
else
    echo "启动失败，qemu.log：" >&2
    cat "$RUNDIR/qemu.log" >&2
    exit 1
fi
