#!/usr/bin/env bash
# 把 qemu/patches/ 中的 C2900 fuzz 插桩补丁应用到 QEMU 源码树。
#
# 用法：
#   ./apply-patch.sh [qemu-source-tree] [--check|--apply|--revert]
#
# 默认源码树：<项目>/vendor/qemu/src（由 scripts/prepare-qemu-src.sh 解包）
# 补丁基线：qemu-8.2-eval-v3.tar.gz（sha256 ce6cbdac...ea579ab）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PATCH="$HERE/patches/0001-c2900-fuzz-overlay.patch"

MODE="apply"
TARGET=""
for arg in "$@"; do
    case "$arg" in
        --check)  MODE="check" ;;
        --apply)  MODE="apply" ;;
        --revert) MODE="revert" ;;
        -h|--help)
            sed -n '2,10p' "$0"; exit 0 ;;
        -*)
            echo "未知参数：$arg" >&2; exit 2 ;;
        *)
            TARGET="$arg" ;;
    esac
done

TARGET="${TARGET:-${QEMU_SRC:-$ROOT/vendor/qemu/src}}"
TARGET="$(cd "$TARGET" && pwd)"

if [ ! -f "$TARGET/VERSION" ] || [ ! -d "$TARGET/accel/tcg" ]; then
    echo "错误：$TARGET 不像 QEMU 源码树（缺 VERSION 或 accel/tcg）" >&2
    exit 1
fi
if [ ! -f "$TARGET/hw/mips/soc_eval.c" ] || \
   ! grep -q 'DEFINE_MACHINE("octeon"' "$TARGET/hw/mips/soc_eval.c"; then
    echo "错误：$TARGET 不是 qemu-8.2-eval-v2/v3（hw/mips/soc_eval.c 缺少 octeon 机器）" >&2
    exit 1
fi
if [ ! -f "$PATCH" ]; then
    echo "错误：找不到补丁 $PATCH" >&2
    exit 1
fi
command -v patch >/dev/null 2>&1 || {
    echo "错误：需要 GNU patch（请安装 patch）" >&2; exit 1; }

is_applied() {
    grep -q 'fuzz_coverage_record_tb' "$TARGET/accel/tcg/cpu-exec.c" 2>/dev/null
}

# 用 GNU patch 而不是 git apply：目标树位于项目 Git 工作区内时，
# `git -C <target> apply` 会以仓库根为基准，导致所有路径被“跳过”。
PATCH_ARGS=(-d "$TARGET" -p1 --no-backup-if-mismatch)

case "$MODE" in
    check)
        if is_applied; then
            echo "补丁已应用：$TARGET"
        elif patch "${PATCH_ARGS[@]}" --dry-run --forward < "$PATCH" >/dev/null 2>&1; then
            echo "补丁可应用：$TARGET"
        else
            echo "补丁无法应用（基线不符或文件已被修改）：$TARGET" >&2
            exit 1
        fi
        ;;
    apply)
        if is_applied; then
            echo "补丁已应用，跳过：$TARGET"
            exit 0
        fi
        if patch "${PATCH_ARGS[@]}" --dry-run --forward < "$PATCH" >/dev/null 2>&1; then
            patch "${PATCH_ARGS[@]}" --forward < "$PATCH"
            echo "已应用：$(basename "$PATCH") -> $TARGET"
        else
            echo "错误：补丁无法应用。请确认源码树是未修改的 qemu-8.2-eval-v3。" >&2
            exit 1
        fi
        ;;
    revert)
        if ! is_applied; then
            echo "补丁未应用，无需回滚：$TARGET"
            exit 0
        fi
        patch "${PATCH_ARGS[@]}" -R < "$PATCH"
        echo "已回滚：$(basename "$PATCH") <- $TARGET"
        ;;
esac
