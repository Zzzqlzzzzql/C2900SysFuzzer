#!/usr/bin/env bash
# 构建带 fuzz 插桩的 qemu-system-mips64。
#
# 用法：
#   ./build-qemu.sh [源码树]
# 默认源码树：<项目>/vendor/qemu/src
#
# 若源码树不存在，会自动从项目内的
#   vendor/qemu/qemu-8.2-eval-v3.tar.gz
# 解包并应用 qemu/patches/0001-c2900-fuzz-overlay.patch。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-${QEMU_SRC:-$ROOT/vendor/qemu/src}}"
mkdir -p "$(dirname "$SRC")"
SRC="$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")"

if [ ! -f "$SRC/VERSION" ]; then
    if [ -f "$ROOT/vendor/qemu/qemu-8.2-eval-v3.tar.gz" ]; then
        "$ROOT/scripts/prepare-qemu-src.sh" --dest "$SRC"
    else
        echo "错误：$SRC 不是 QEMU 源码树，且找不到 vendor/qemu/qemu-8.2-eval-v3.tar.gz" >&2
        echo "请把源码包放到 vendor/qemu/（见 docs/PRIVATE-ASSETS.md），或把已解包的源码树路径作为参数传入。" >&2
        exit 1
    fi
fi

# 本项目只支持 qemu-8.2-eval-v2/v3（octeon 机器 + octeon-eth + machcore）。
if ! grep -q 'DEFINE_MACHINE("octeon"' "$SRC/hw/mips/soc_eval.c" 2>/dev/null; then
    echo "错误：$SRC 不是 qemu-8.2-eval-v2/v3（hw/mips/soc_eval.c 缺少 octeon 机器）" >&2
    exit 1
fi

"$ROOT/qemu/apply-patch.sh" "$SRC"

echo "源码树：$SRC"
echo "版本：$(cat "$SRC/VERSION")"

if [ ! -d "$SRC/build" ]; then
    echo "未发现 build/，执行 configure（mips64-softmmu、无 pixman/slirp）..."
    (cd "$SRC" && ./configure --target-list=mips64-softmmu -Dpixman=disabled \
        --disable-gtk --disable-sdl --disable-vnc --disable-docs \
        --disable-werror --disable-guest-agent)
fi

ninja -C "$SRC/build" qemu-system-mips64

# 同步运行产物到 vendor/qemu/bin 与 lib，供分发包直接运行。
# 先删除目标：它们可能是 prepare-private-assets.sh --link 建立的符号链接，
# 直接 cp -f 会顺着链接覆盖私有资产原件。
mkdir -p "$ROOT/vendor/qemu/bin" "$ROOT/vendor/qemu/lib"
rm -f "$ROOT/vendor/qemu/bin/qemu-system-mips64"
cp -f "$SRC/build/qemu-system-mips64" "$ROOT/vendor/qemu/bin/"
if [ -f "$SRC/roms/machcore/libmachcore.so" ]; then
    rm -f "$ROOT/vendor/qemu/lib/libmachcore.so"
    cp -f "$SRC/roms/machcore/libmachcore.so" "$ROOT/vendor/qemu/lib/"
fi
echo
echo "构建完成：$SRC/build/qemu-system-mips64"
echo "已同步到：$ROOT/vendor/qemu/bin/qemu-system-mips64"
