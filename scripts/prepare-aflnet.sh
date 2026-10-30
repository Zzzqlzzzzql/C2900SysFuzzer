#!/usr/bin/env bash
# 拉取官方 AFLNet、钉到指定 commit、应用 -Z（QEMU 系统模式）补丁并编译。
#
# 官方仓库：https://github.com/aflnet/aflnet
# 补丁：patches/aflnet.patch（来自 QemuSysFuzzer 的 QEMU 系统模式改动）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AFL_DIR="$ROOT/vendor/aflnet"
PATCH="$ROOT/patches/aflnet.patch"
PIN="96032f86d0005dfeeb41ea7b31103f1d1ff8f168"
URL="https://github.com/aflnet/aflnet.git"

info() { printf '== %s\n' "$*"; }
die()  { echo "错误：$*" >&2; exit 1; }

if [ ! -f "$AFL_DIR/Makefile" ]; then
    info "初始化 AFLNet 子模块"
    if git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
        git -C "$ROOT" submodule update --init --recursive vendor/aflnet || true
    fi
    if [ ! -f "$AFL_DIR/Makefile" ]; then
        info "子模块不可用，直接 clone 官方 AFLNet"
        rm -rf "$AFL_DIR.tmp"
        git clone "$URL" "$AFL_DIR.tmp"
        git -C "$AFL_DIR.tmp" checkout -q "$PIN"
        rm -rf "$AFL_DIR"
        mv "$AFL_DIR.tmp" "$AFL_DIR"
    fi
fi
[ -f "$AFL_DIR/Makefile" ] || die "AFLNet 目录不可用：$AFL_DIR"

HEAD="$(git -C "$AFL_DIR" rev-parse HEAD 2>/dev/null || true)"
if [ "$HEAD" != "$PIN" ]; then
    info "AFLNet HEAD=$HEAD，切换到 $PIN"
    if git -C "$AFL_DIR" diff --quiet && git -C "$AFL_DIR" diff --cached --quiet; then
        git -C "$AFL_DIR" checkout -q "$PIN"
    else
        die "AFLNet 不在固定 commit 且存在本地修改；请先清理 $AFL_DIR"
    fi
fi

if ! grep -q 'qemu_system_mode' "$AFL_DIR/afl-fuzz.c"; then
    info "应用 AFLNet -Z 系统模式补丁"
    [ -f "$PATCH" ] || die "缺少补丁 $PATCH"
    patch -d "$AFL_DIR" -p1 --forward < "$PATCH"
else
    info "AFLNet -Z 补丁已应用"
fi

info "编译 AFLNet"
make -C "$AFL_DIR" -j"$(nproc)"
echo "AFLNet 就绪：$AFL_DIR/afl-fuzz"
