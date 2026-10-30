#!/usr/bin/env bash
# 把私下获取的 C2900 资产准备到项目内（或建立符号链接）。
#
# 资产包目录结构（见 docs/PRIVATE-ASSETS.md）：
#   <assets>/
#   ├── qemu/
#   │   ├── src/                       # v3 已解包源码 + 已补齐 subprojects（优先）
#   │   ├── bin/qemu-system-mips64     # 可选，预编译插桩版
#   │   └── lib/libmachcore.so         # 闭源设备库 v3
#   └── c2900/
#       ├── firmware/                  # 固件/磁盘镜像
#       └── lib/                       # 运行库（可选）
#
# 旧 tarball 回退：qemu-8.2-eval-v3.tar.gz + qemu-subprojects-fix.tar.gz
#
# 用法：
#   ./scripts/prepare-private-assets.sh <assets-dir> [--link] [--no-src] [--force]
#
#   --link     用符号链接代替复制（固件较大时推荐）
#   --no-src   不安装 QEMU 源码（只准备运行时资产）
#   --force    重新安装 vendor/qemu/src（会删除现有目录）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS=""
LINK=0
NO_SRC=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --link)   LINK=1 ;;
        --no-src) NO_SRC=1 ;;
        --force)  FORCE=1 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        -*) echo "未知参数：$arg" >&2; exit 2 ;;
        *)  ASSETS="$arg" ;;
    esac
done

if [ -z "$ASSETS" ] || [ ! -d "$ASSETS" ]; then
    echo "用法：$0 <assets-dir> [--link] [--no-src] [--force]" >&2
    exit 2
fi
ASSETS="$(cd "$ASSETS" && pwd)"

info() { printf '== %s\n' "$*"; }
die()  { echo "错误：$*" >&2; exit 1; }

install_file() { # src dst
    local src="$1" dst="$2"
    mkdir -p "$(dirname "$dst")"
    if [ "$LINK" -eq 1 ]; then
        ln -sfn "$(readlink -f "$src")" "$dst"
    else
        cp -f "$src" "$dst"
    fi
}
install_dir() { # srcdir dstdir
    local src="$1" dst="$2" f base
    [ -d "$src" ] || return 0
    mkdir -p "$dst"
    for f in "$src"/*; do
        [ -f "$f" ] || continue
        base="$(basename "$f")"
        # 仓库自带的校验清单/说明不要被资产包覆盖
        case "$base" in checksums.sha256|README.md) continue ;; esac
        install_file "$f" "$dst/$base"
    done
}

if [ "$NO_SRC" -eq 0 ]; then
    if [ -f "$ASSETS/qemu/src/VERSION" ]; then
        info "安装目录形式的 QEMU 源码 -> vendor/qemu/src"
        [ -f "$ASSETS/qemu/src/subprojects/berkeley-softfloat-3/meson.build" ] || \
            die "资产包 qemu/src/subprojects 不完整"
        if [ ! -f "$ROOT/vendor/qemu/src/VERSION" ] || [ "$FORCE" -eq 1 ]; then
            rm -rf "$ROOT/vendor/qemu/src"
            cp -a "$ASSETS/qemu/src" "$ROOT/vendor/qemu/src"
        else
            info "vendor/qemu/src 已存在，保留"
        fi
        "$ROOT/qemu/apply-patch.sh" "$ROOT/vendor/qemu/src"
    else
        TARBALL="$ASSETS/qemu-8.2-eval-v3.tar.gz"
        FIX="$ASSETS/qemu-subprojects-fix.tar.gz"
        # 资产包不带源码包时，允许使用项目内放置的副本
        if [ ! -f "$TARBALL" ] && [ -f "$ROOT/vendor/qemu/qemu-8.2-eval-v3.tar.gz" ]; then
            info "资产包无源码目录，改用 vendor/qemu/qemu-8.2-eval-v3.tar.gz"
            TARBALL="$ROOT/vendor/qemu/qemu-8.2-eval-v3.tar.gz"
            [ -f "$FIX" ] || FIX="$ROOT/vendor/qemu/qemu-subprojects-fix.tar.gz"
        fi
        [ -f "$TARBALL" ] || die "缺少 $ASSETS/qemu/src/VERSION 或 $TARBALL（或使用 --no-src）"

        if [ -f "$ROOT/vendor/qemu/src/VERSION" ] && [ "$FORCE" -eq 0 ]; then
            info "vendor/qemu/src 已存在，确保补丁已应用"
            "$ROOT/qemu/apply-patch.sh" "$ROOT/vendor/qemu/src"
        else
            PREPARE_ARGS=(--tarball "$TARBALL")
            [ -f "$FIX" ] && PREPARE_ARGS+=(--fix "$FIX")
            [ "$FORCE" -eq 1 ] && PREPARE_ARGS+=(--force)
            "$ROOT/scripts/prepare-qemu-src.sh" "${PREPARE_ARGS[@]}"
        fi
    fi
fi

if [ -f "$ASSETS/qemu/lib/libmachcore.so" ]; then
    info "安装 libmachcore.so"
    install_file "$ASSETS/qemu/lib/libmachcore.so" "$ROOT/vendor/qemu/lib/libmachcore.so"
fi
if [ -f "$ASSETS/qemu/bin/qemu-system-mips64" ]; then
    info "安装预编译 qemu-system-mips64"
    install_file "$ASSETS/qemu/bin/qemu-system-mips64" "$ROOT/vendor/qemu/bin/qemu-system-mips64"
    chmod +x "$ROOT/vendor/qemu/bin/qemu-system-mips64"
fi

if [ -d "$ASSETS/c2900/firmware" ]; then
    info "安装 C2900 固件/磁盘镜像"
    install_dir "$ASSETS/c2900/firmware" "$ROOT/vendor/c2900/firmware"
fi
if [ -d "$ASSETS/c2900/lib" ]; then
    info "安装运行库"
    install_dir "$ASSETS/c2900/lib" "$ROOT/vendor/c2900/lib"
fi

echo
echo "私有资产准备完成（link=$LINK, no_src=$NO_SRC）。"
if [ "$LINK" -eq 1 ]; then
    echo "注意：--link 模式下列表指向私有资产原件；vendor/c2900/test/start_c2900.sh" \
         "等不带 -snapshot 的启动方式可能写回 flash/rommon/disk0，建议改回复制模式。"
fi
echo "下一步：./scripts/check-assets.sh && ./scripts/check-env.sh"
