#!/usr/bin/env bash
set -e

PKG="$(pwd)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
QEMU_BIN="${C2900_QEMU:-$PKG/bin/qemu-system-mips64}"
if [ ! -x "$QEMU_BIN" ]; then
	QEMU_BIN="$SCRIPT_DIR/../../qemu/bin/qemu-system-mips64"
fi
export LD_LIBRARY_PATH="$PKG/lib:$SCRIPT_DIR/../../qemu/lib:$LD_LIBRARY_PATH"

# 可选：覆盖率采集插件（C2900_COVERAGE=1 启用，实时日志写入 $PKG/logs/coverage.log）
COVERAGE_OPT=()
if [ "${C2900_COVERAGE:-0}" = "1" ]; then
	COVERAGE_OPT=(-plugin "file=$PKG/lib/libcoverage2.so")
	echo "coverage: 已启用，日志：$PKG/logs/coverage.log（对数级采样，体积有界）"
fi

if tmux has-session -t console 2>/dev/null; then
	tmux kill-session -t console
fi

if tmux has-session -t qemu 2>/dev/null; then
	tmux kill-session -t qemu
fi

"$SCRIPT_DIR/set_tap.sh"

tmux new-session -d -s qemu env "LD_LIBRARY_PATH=$LD_LIBRARY_PATH" "$QEMU_BIN" \
	-cpu Octeon52XX -m 512 -M octeon \
	-kernel "$PKG/firmware/C2900-UN.150.BIN" \
	-bios "$PKG/firmware/mips64_mixed.bin" \
	-drive "file=$PKG/firmware/rommon_var.bin,format=raw,id=iomem" \
	-drive "if=pflash,unit=1,format=raw,file=$PKG/firmware/flash.bin" \
	-display none \
	-chardev stdio,id=char0,mux=on \
	-serial chardev:char0 \
	-mon chardev=char0,mode=readline \
	-chardev socket,id=char1,host=127.0.0.1,port=5554,server=on,wait=off \
	-serial chardev:char1 \
	-drive "file=$PKG/firmware/disk0.raw,id=disk0,format=raw" \
	-icount shift=1,sleep=off \
	-nic tap,model=octeon-eth,ifname=tap0,script=no,downscript=no,mac=52:54:00:12:34:56 \
	"${COVERAGE_OPT[@]}"

sleep 3
tmux new-session -d -s console python3 "$SCRIPT_DIR/connect_console.py"
tmux attach-session -t console
