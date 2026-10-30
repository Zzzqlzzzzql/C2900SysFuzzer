#!/usr/bin/env bash
# 覆盖率插件试运行脚本：qemu 在 tmux 后台运行，不 attach、不占用终端输出。
#
# 与 start_c2900.sh 的差异：
#   - 不配置 tap0，改用 -nic user,model=octeon-eth（不需要 sudo，也不影响在用实例）
#   - -snapshot：guest 写入进临时 overlay，不改动 flash.bin/rommon_var.bin/disk0.raw
#   - 覆盖率插件常开，日志写 $PKG/logs/coverage.log（QEMU 启动时截断重写）
#   - qemu 输出（串口 + monitor）只留在 tmux pane 里，脚本本身不产生终端刷屏
#
# 用法：
#   ./test/test_coverage.sh          # 启动（若已在运行则提示，不会打断）
#   ./test/test_coverage.sh stop     # 停止（先 SIGTERM 让插件落最终快照，再兜底杀会话）
#   tmux attach -t covtest           # 查看 qemu 串口/monitor 输出（Ctrl-b d 脱离）
#   python3 test/connect_console.py  # 串口另开 socket：127.0.0.1:5555
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG="$(cd "$SCRIPT_DIR/.." && pwd)"
SESSION=covtest
SERIAL_PORT=5555

if [ "${1:-}" = "stop" ]; then
	if ! tmux has-session -t "$SESSION" 2>/dev/null; then
		echo "coverage 试运行未在运行（无 tmux 会话 '$SESSION'）"
		exit 0
	fi
	# pane_pid 通常就是 qemu（tmux 直接 exec）；若是 shell 包裹则取其子进程
	pid="$(tmux list-panes -t "$SESSION" -F '#{pane_pid}' | head -1)"
	if ! tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q qemu-system-mips64; then
		pid="$(pgrep -P "$pid" -f qemu-system-mips64 | head -1 || true)"
	fi
	if [ -n "$pid" ]; then
		kill -TERM "$pid" 2>/dev/null || true
		for _ in $(seq 1 10); do
			kill -0 "$pid" 2>/dev/null || break
			sleep 1
		done
	fi
	tmux kill-session -t "$SESSION" 2>/dev/null || true
	echo "coverage 试运行已停止，日志：$PKG/logs/coverage.log"
	exit 0
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
	echo "coverage 试运行已在运行（tmux attach -t $SESSION 查看，$0 stop 停止）"
	exit 0
fi

export LD_LIBRARY_PATH="$PKG/lib:$LD_LIBRARY_PATH"
mkdir -p "$PKG/logs"

tmux new-session -d -s "$SESSION" -c "$PKG" \
	env "LD_LIBRARY_PATH=$LD_LIBRARY_PATH" "$PKG/bin/qemu-system-mips64" \
	-cpu Octeon52XX -m 512 -M octeon \
	-kernel "$PKG/firmware/C2900-UN.150.BIN" \
	-bios "$PKG/firmware/mips64_mixed.bin" \
	-drive "file=$PKG/firmware/rommon_var.bin,format=raw,id=iomem" \
	-drive "if=pflash,unit=1,format=raw,file=$PKG/firmware/flash.bin" \
	-display none \
	-chardev stdio,id=char0,mux=on \
	-serial chardev:char0 \
	-mon chardev=char0,mode=readline \
	-chardev socket,id=char1,host=127.0.0.1,port=$SERIAL_PORT,server=on,wait=off \
	-serial chardev:char1 \
	-drive "file=$PKG/firmware/disk0.raw,id=disk0,format=raw" \
	-icount shift=1,sleep=off \
	-nic user,model=octeon-eth,mac=52:54:00:12:34:56 \
	-snapshot \
	-plugin "file=$PKG/lib/libcoverage2.so"

sleep 2
if tmux has-session -t "$SESSION" 2>/dev/null; then
	echo "coverage 试运行已启动（tmux 会话：$SESSION，后台运行，无终端输出）"
	echo "  覆盖率日志：$PKG/logs/coverage.log（对数级采样，体积有界）"
	echo "  串口 socket：127.0.0.1:$SERIAL_PORT（tmux attach -t $SESSION 可看串口/monitor）"
	echo "  停止：$0 stop"
else
	echo "启动失败：qemu 未能驻留 tmux 会话（检查参数/端口占用），可手动原样跑一遍看报错" >&2
	exit 1
fi
