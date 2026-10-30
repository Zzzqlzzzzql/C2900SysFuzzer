#!/usr/bin/env bash
# 检查运行 C2900 系统级 fuzz 原型所需的宿主环境、QEMU 产物与固件文件。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
QEMU="${C2900_QEMU:-$ROOT/vendor/qemu/bin/qemu-system-mips64}"
QEMU_LIB="${C2900_QEMU_LIB:-$ROOT/vendor/qemu/lib}"
PKG="${C2900_PKG:-$ROOT/vendor/c2900}"
export LD_LIBRARY_PATH="$QEMU_LIB:$PKG/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fail=0

ok()   { printf '  [ OK ] %s\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; fail=1; }
info() { printf '  [info] %s\n' "$*"; }

echo "== 宿主工具 =="
for c in bash git make patch tmux socat python3 ninja cc pkg-config; do
    if command -v "$c" >/dev/null 2>&1; then
        ok "$c: $(command -v "$c")"
    else
        bad "$c 未安装"
    fi
done
if command -v pkg-config >/dev/null 2>&1; then
    if pkg-config --exists glib-2.0; then
        ok "glib-2.0: $(pkg-config --modversion glib-2.0)"
    else
        bad "缺少 glib-2.0 开发包（QEMU 构建需要）"
    fi
fi

echo "== QEMU =="
if [ -x "$QEMU" ]; then
    ok "QEMU: $QEMU"
    info "$("$QEMU" --version 2>/dev/null | head -1)"
    if "$QEMU" --help 2>&1 | grep -q -- "-fuzz"; then
        ok "QEMU 含 -fuzz 选项"
    else
        bad "QEMU 未包含 -fuzz（qemu/patches 未应用或未重建）"
    fi
    if "$QEMU" -M help 2>&1 | grep -q "octeon"; then
        ok "QEMU 支持 -M octeon"
    else
        bad "QEMU 不支持 -M octeon（不是 C2900 v2 源码基线）"
    fi
else
    bad "QEMU 不存在：$QEMU"
fi
PATCH="$ROOT/qemu/patches/0001-c2900-fuzz-overlay.patch"
[ -f "$PATCH" ] && ok "QEMU 插桩补丁：$(basename "$PATCH")" || bad "缺少 $PATCH"
[ -f "$ROOT/vendor/qemu/src/VERSION" ] && ok "QEMU 源码：$ROOT/vendor/qemu/src（$(cat "$ROOT/vendor/qemu/src/VERSION")）" \
    || info "未找到 vendor/qemu/src（运行预编译二进制不需要；重建时需要）"
if [ -f "$ROOT/vendor/qemu/src/accel/tcg/cpu-exec.c" ] && \
   grep -q 'fuzz_coverage_record_tb' "$ROOT/vendor/qemu/src/accel/tcg/cpu-exec.c"; then
    ok "QEMU 源码已应用 fuzz 补丁"
fi
[ -d "$QEMU_LIB" ] && ok "QEMU 运行库：$QEMU_LIB" || bad "QEMU 运行库不存在：$QEMU_LIB"
[ -f "$QEMU_LIB/libmachcore.so" ] && ok "设备库：$QEMU_LIB/libmachcore.so" \
    || bad "缺少闭源设备库：$QEMU_LIB/libmachcore.so"
[ -f "$PKG/test/connect_console.py" ] && ok "console 脚本：$PKG/test/connect_console.py" \
    || bad "缺少 $PKG/test/connect_console.py"

echo "== C2900 固件/磁盘 =="
for f in \
    "$PKG/firmware/mips64_mixed.bin" \
    "$PKG/firmware/rommon_var.bin" \
    "$PKG/firmware/flash.bin" \
    "$PKG/firmware/disk0.raw" \
    "$PKG/firmware/C2900-UN.150.BIN" \
    "$PKG/firmware/c2900-universalk9-mz.SPA.150-1.M9.bin"; do
    if [ -f "$f" ]; then
        ok "$(basename "$f") ($(stat -Lc%s "$f") B)"
    else
        bad "缺少 $f"
    fi
done

echo "== 原型文件 =="
for f in \
    "$ROOT/runtime/start_c2900_fuzz.sh" \
    "$ROOT/runtime/fuzz_client.py" \
    "$ROOT/runtime/coverage_probe.py" \
    "$ROOT/runtime/c2900-ios.json" \
    "$ROOT/runtime/c2900-ios-m7.json" \
    "$ROOT/scripts/verify-coverage.sh" \
    "$ROOT/scripts/verify-aflnet.sh" \
    "$ROOT/scripts/verify-crash.sh"; do
    [ -f "$f" ] && ok "$(basename "$f")" || bad "缺少 $f"
done

AFL="${AFL_FUZZ:-$ROOT/vendor/aflnet/afl-fuzz}"
if [ -x "$AFL" ]; then
    ok "AFLNet: $AFL"
elif [ -f "$ROOT/vendor/aflnet/Makefile" ]; then
    info "AFLNet 子模块已就绪但未编译：make -C vendor/aflnet -j"
else
    info "AFLNet 子模块未初始化：git submodule update --init --recursive"
fi
if [ -f "$ROOT/vendor/aflnet/afl-fuzz.c" ]; then
    if grep -q 'qemu_system_mode' "$ROOT/vendor/aflnet/afl-fuzz.c"; then
        ok "AFLNet -Z 补丁已应用"
    else
        info "AFLNet -Z 补丁未应用：./scripts/prepare-aflnet.sh"
    fi
    head="$(git -C "$ROOT/vendor/aflnet" rev-parse --short HEAD 2>/dev/null || true)"
    [ -n "$head" ] && info "AFLNet commit：$head（期望 96032f8）"
fi
for s in config_web.py config_ssh.py config_snmp.py config_telnet.py \
         config_ipsec_license.py config_ipsec.py; do
    [ -f "$PKG/test/$s" ] && ok "协议配置脚本：$s" || bad "缺少 $PKG/test/$s"
done
for p in http https ssh snmp ipsec telnet; do
    d="$ROOT/runtime/protocols/$p"
    if [ -f "$d/protocol.env" ] && [ -f "$d/config/fuzz.json" ]; then
        ok "协议区域：$p"
    else
        bad "协议区域不完整：$p"
    fi
done

echo "== 运行环境 =="
info "tmux 会话："
tmux ls 2>/dev/null | sed 's/^/    /' || true
info "forkserver IPC："
if [ -p /tmp/qemu_fuzz_ctl ] || [ -p /tmp/qemu_fuzz_st ]; then
    echo "    /tmp/qemu_fuzz_ctl 或 /tmp/qemu_fuzz_st 存在（可能有实例在运行）"
else
    echo "    无"
fi
info "共享内存 0x2000/0x2001："
ipcs -m 2>/dev/null | awk '$1 ~ /0x0000200/ {print "    "$0}' || true

echo
if [ "$fail" -eq 0 ]; then
    echo "环境检查通过。"
else
    echo "环境检查发现缺失项，请先修复。" >&2
fi
exit "$fail"
