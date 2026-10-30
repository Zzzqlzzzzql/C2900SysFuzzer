#!/usr/bin/env bash
# 准备 QEMU 源码树并应用 fuzz 补丁。
#
# 首选：私有分发包已经提供解包好的目录
#   vendor/qemu/src/            （目录形式，已补齐 subprojects）
# 回退：没有目录时，从 tarball 解包
#   vendor/qemu/qemu-8.2-eval-v3.tar.gz
#   vendor/qemu/qemu-subprojects-fix.tar.gz
#
# 用法：
#   ./scripts/prepare-qemu-src.sh [--tarball PATH] [--fix PATH] [--dest DIR]
#                                 [--force] [--skip-checksum]
#
#   --force          重新解包（会删除目标目录）
#   --skip-checksum  跳过已知 SHA256 校验（不推荐）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARBALL="${QEMU_TARBALL:-$ROOT/vendor/qemu/qemu-8.2-eval-v3.tar.gz}"
FIX="${QEMU_SUBPROJECTS_FIX:-$ROOT/vendor/qemu/qemu-subprojects-fix.tar.gz}"
DEST="${QEMU_SRC:-$ROOT/vendor/qemu/src}"
FORCE=0
SKIP_SUM=0

while [ $# -gt 0 ]; do
    case "$1" in
        --tarball) TARBALL="$2"; shift 2 ;;
        --fix)     FIX="$2"; shift 2 ;;
        --dest)    DEST="$2"; shift 2 ;;
        --force)   FORCE=1; shift ;;
        --skip-checksum) SKIP_SUM=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "未知参数：$1" >&2; exit 2 ;;
    esac
done

TARBALL_SHA="ce6cbdace15a3e6973d9e6b5acebc58b1cf001531e9d34cbcd97f4c51ea579ab"
FIX_SHA="e49ceac2a78ad77c5640e2d26a914c3966a76f1f94728b94f01fb8a88a0ade13"
mkdir -p "$(dirname "$DEST")"
DEST="$(cd "$(dirname "$DEST")" && pwd)/$(basename "$DEST")"

die() { echo "错误：$*" >&2; exit 1; }
info() { printf '== %s\n' "$*"; }

# 已有可用的源码树（目录形式私有包或上次构建）：只确保补丁已应用
if [ -f "$DEST/VERSION" ] && [ "$FORCE" -eq 0 ]; then
    info "已存在 $DEST（VERSION=$(cat "$DEST/VERSION")），使用现有目录"
    "$ROOT/qemu/apply-patch.sh" "$DEST"
    exit 0
fi

[ -f "$TARBALL" ] || die "找不到源码目录 $DEST，也找不到源码包 $TARBALL。
目录形式的私有分发包应包含 qemu/src/；或把 qemu-8.2-eval-v3.tar.gz 放到 vendor/qemu/。"

if [ "$SKIP_SUM" -eq 0 ]; then
    actual="$(sha256sum "$TARBALL" | awk '{print $1}')"
    [ "$actual" = "$TARBALL_SHA" ] || die "源码包 SHA256 不匹配（期望 $TARBALL_SHA，实际 $actual）"
    info "源码包 SHA256 校验通过"
else
    echo "警告：已跳过源码包 SHA256 校验" >&2
fi

if [ -e "$DEST" ] && [ "$FORCE" -eq 0 ]; then
    [ -d "$DEST" ] && [ -z "$(ls -A "$DEST" 2>/dev/null)" ] || \
        die "$DEST 已存在但不是有效的 QEMU 源码树；确认可删除后加 --force"
fi

if [ ! -f "$FIX" ] && [ "$SKIP_SUM" -eq 0 ]; then
    echo "警告：找不到 $FIX；v3 源码包的 subprojects 不完整，构建可能失败" >&2
elif [ -f "$FIX" ] && [ "$SKIP_SUM" -eq 0 ]; then
    actual="$(sha256sum "$FIX" | awk '{print $1}')"
    [ "$actual" = "$FIX_SHA" ] || die "subprojects 修复包 SHA256 不匹配（期望 $FIX_SHA，实际 $actual）"
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/c2900-qemu-src.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

info "解包 $TARBALL -> $DEST"
tar -xzf "$TARBALL" --strip-components=1 --no-same-owner -C "$TMP"
[ -f "$TMP/VERSION" ] || die "解包后缺少 VERSION，源码包结构不符合预期"
grep -q 'DEFINE_MACHINE("octeon"' "$TMP/hw/mips/soc_eval.c" 2>/dev/null || \
    die "源码树不是 qemu-8.2-eval-v2/v3（缺少 octeon 机器）"

if [ -f "$FIX" ]; then
    info "补齐 v3 subprojects"
    tar -xzf "$FIX" --no-same-owner -C "$TMP/subprojects"
fi

if [ "$FORCE" -eq 1 ] && [ -e "$DEST" ]; then
    rm -rf "$DEST"
fi
mkdir -p "$(dirname "$DEST")"
mv "$TMP" "$DEST"
trap - EXIT

info "应用 fuzz 补丁"
"$ROOT/qemu/apply-patch.sh" "$DEST"
echo
echo "QEMU 源码树就绪：$DEST"
echo "下一步：./scripts/build-qemu.sh"
