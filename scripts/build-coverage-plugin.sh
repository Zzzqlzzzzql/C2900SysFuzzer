#!/usr/bin/env bash
# 从 tools/coverage2.c 构建 TCG 覆盖率插件 libcoverage2.so。
#
# 依赖：QEMU 源码头文件（vendor/qemu/src，私有资产或本地解包）+ glib-2.0 开发包。
# 产物：vendor/c2900/lib/libcoverage2.so
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${QEMU_SRC:-$ROOT/vendor/qemu/src}"
OUT="${1:-$ROOT/vendor/c2900/lib/libcoverage2.so}"
CC="${CC:-cc}"

if [ ! -f "$SRC/include/qemu/module.h" ]; then
    echo "错误：找不到 QEMU 源码树 $SRC（设置 QEMU_SRC）" >&2
    exit 1
fi
if ! pkg-config --exists glib-2.0 2>/dev/null; then
    echo "错误：缺少 glib-2.0 开发包（pkg-config glib-2.0）" >&2
    exit 1
fi

mkdir -p "$(dirname "$OUT")"
"$CC" -m64 -mcx16 -O2 -fPIC -Wall \
    $(pkg-config --cflags glib-2.0) \
    -I"$SRC/include/qemu" -I"$SRC/include" \
    -shared -o "$OUT" "$ROOT/tools/coverage2.c" \
    $(pkg-config --libs glib-2.0)

echo "已构建：$OUT"
