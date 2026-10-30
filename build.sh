#!/usr/bin/env bash
# C2900SysFuzzer 一键构建：
#   1) 安装 vendor/ 下的目录形式私有资产（qemu/src + 固件 + 设备库 + 运行库）
#   2) 拉取官方 AFLNet（submodule）并应用 -Z 补丁、编译
#   3) 安装/校验 QEMU 源码、应用 fuzz 补丁并构建
#   4) 校验资产/环境，执行启动脚本 smoke test，打印需要手动填写的配置
#
# 用法：
#   cp -a c2900-private-assets vendor/
#   ./build.sh [--skip-smoke] [--full] [--force-assets]
#
#   --skip-smoke    跳过启动脚本 smoke test
#   --full          启动到 IOS banner（约 20 分钟）后再停止；默认只做暂停态启动检查
#   --force-assets  即使 vendor 已有资产也重新安装资产包
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

SKIP_SMOKE=0
FULL_BOOT=0
FORCE_ASSETS=0
while [ $# -gt 0 ]; do
    case "$1" in
        --skip-smoke)   SKIP_SMOKE=1 ;;
        --full)         FULL_BOOT=1 ;;
        --force-assets) FORCE_ASSETS=1 ;;
        -h|--help)      sed -n '2,14p' "$0"; exit 0 ;;
        *) echo "未知参数：$1" >&2; exit 2 ;;
    esac
    shift
done

info() { printf '\n== %s\n' "$*"; }
die()  { echo "错误：$*" >&2; exit 1; }

for c in bash git make patch tar; do
    command -v "$c" >/dev/null 2>&1 || die "缺少命令：$c"
done

assets_ready() {
    [ -f vendor/qemu/src/VERSION ] && \
    [ -f vendor/qemu/src/hw/mips/soc_eval.c ] && \
    [ -f vendor/qemu/lib/libmachcore.so ] || return 1
    for f in C2900-UN.150.BIN c2900-universalk9-mz.SPA.150-1.M9.bin \
             disk0.raw flash.bin mips64_mixed.bin rommon_var.bin; do
        [ -f "vendor/c2900/firmware/$f" ] || return 1
    done
    return 0
}

PKG=""
for f in vendor/*private-assets*.tar.zst vendor/*private-assets*.tar.gz; do
    if [ -f "$f" ]; then PKG="$f"; break; fi
done

ASSET_DIR=""
for d in vendor/*private-assets*/; do
    if [ -d "$d" ] && [ -f "$d/qemu/src/VERSION" ]; then
        ASSET_DIR="${d%/}"
        break
    fi
done

install_assets_from_dir() {
    local d="$1"
    [ -f "$d/qemu/src/VERSION" ] || die "资产包缺少 qemu/src/VERSION（需要解包后的源码目录）"
    [ -f "$d/qemu/src/hw/mips/soc_eval.c" ] || die "资产包 qemu/src 不是厂商 v3 源码树"
    [ -f "$d/qemu/src/subprojects/berkeley-softfloat-3/meson.build" ] || \
        die "资产包 qemu/src/subprojects 不完整"
    [ -d "$d/c2900/firmware" ] || die "资产包缺少 c2900/firmware/"

    mkdir -p vendor/qemu vendor/c2900
    if [ ! -f vendor/qemu/src/VERSION ] || [ "$FORCE_ASSETS" -eq 1 ]; then
        info "安装 QEMU 源码目录 -> vendor/qemu/src"
        rm -rf vendor/qemu/src
        cp -a "$d/qemu/src" vendor/qemu/src
    else
        echo "vendor/qemu/src 已存在，保留（--force-assets 可重装）"
    fi
    if [ -d "$d/qemu/lib" ]; then
        cp -a --remove-destination "$d/qemu/lib/." vendor/qemu/lib/
    fi
    cp -a --remove-destination "$d/c2900/." vendor/c2900/
}

info "1/4 私有资产"
if [ "$FORCE_ASSETS" -eq 1 ] || ! assets_ready; then
    if [ -n "$ASSET_DIR" ]; then
        info "使用目录资产包 $(basename "$ASSET_DIR")"
        install_assets_from_dir "$ASSET_DIR"
    elif [ -n "$PKG" ]; then
        info "解压 $(basename "$PKG")"
        case "$PKG" in
            *.tar.zst)
                command -v zstd >/dev/null 2>&1 || die "需要 zstd 解压 .tar.zst（或换用 .tar.gz 包）"
                TMP="$(mktemp -d "$ROOT/vendor/.private.XXXXXX")"
                tar -I 'zstd -d' -xf "$PKG" -C "$TMP"
                ;;
            *.tar.gz)
                TMP="$(mktemp -d "$ROOT/vendor/.private.XXXXXX")"
                tar -xzf "$PKG" -C "$TMP"
                ;;
        esac
        trap 'rm -rf "$TMP"' EXIT
        VER="$(find "$TMP" -maxdepth 4 -path '*/qemu/src/VERSION' -print -quit)"
        [ -n "$VER" ] || die "资产包内缺少 qemu/src/VERSION"
        ASSET_ROOT="${VER%/qemu/src/VERSION}"
        install_assets_from_dir "$ASSET_ROOT"
        rm -rf "$TMP"
        trap - EXIT
    else
        die "vendor/ 下找不到 *private-assets*.tar.zst 或 *private-assets*/ 目录。
请把维护者私下提供的资产包复制到 vendor/ 后重试（见 docs/PRIVATE-ASSETS.md）。"
    fi
    assets_ready || die "资产安装后仍缺少关键文件，请检查资产包结构"
    echo "资产已就位：vendor/qemu/、vendor/c2900/"
else
    echo "vendor/ 中已有资产，跳过安装（--force-assets 可强制重装）"
fi

info "2/4 AFLNet（官方仓库 + -Z 补丁）"
"$ROOT/scripts/prepare-aflnet.sh"

info "3/4 QEMU"
"$ROOT/scripts/prepare-qemu-src.sh"
"$ROOT/scripts/build-qemu.sh"
if [ -f "$ROOT/tools/coverage2.c" ] && \
   [ -f "$ROOT/vendor/qemu/src/include/qemu/module.h" ]; then
    info "构建可选覆盖率插件 libcoverage2.so"
    "$ROOT/scripts/build-coverage-plugin.sh" || \
        echo "警告：libcoverage2.so 构建失败（不影响主 fuzz 流程）" >&2
fi

info "4/4 校验与启动检查"
"$ROOT/scripts/check-assets.sh"
"$ROOT/scripts/check-env.sh"

running_c2900_qemu() {
    local p exe
    for p in /proc/[0-9]*; do
        exe="$(readlink "$p/exe" 2>/dev/null || true)"
        case "$exe" in
            */vendor/qemu/bin/qemu-system-mips64) return 0 ;;
        esac
    done
    return 1
}

if [ "$SKIP_SMOKE" -eq 1 ]; then
    echo
    echo "已跳过启动脚本 smoke test。"
elif running_c2900_qemu; then
    echo
    echo "检测到已有 C2900 QEMU 在运行，跳过 smoke test（避免干扰现有会话）。"
elif [ -e /tmp/qemu_fuzz_ctl ] || [ -e /tmp/qemu_fuzz_st ]; then
    echo
    echo "检测到 forkserver FIFO，跳过 smoke test。"
else
    info "启动脚本 smoke test"
    export C2900_FUZZ_SESSION=c2900-build-smoke
    export C2900_CONSOLE_SESSION=c2900-build-console
    export C2900_FUZZ_RUNDIR="$ROOT/runtime/run/build-smoke"
    export C2900_CONSOLE_PORT="${C2900_BUILD_CONSOLE_PORT:-15554}"
    export C2900_NET_PORT="${C2900_BUILD_NET_PORT:-145678}"
    cleanup_smoke() {
        "$ROOT/runtime/start_c2900_fuzz.sh" stop >/dev/null 2>&1 || true
    }
    trap cleanup_smoke EXIT
    if [ "$FULL_BOOT" -eq 1 ]; then
        unset C2900_FUZZ_START_PAUSED
        export C2900_FUZZ_RUNDIR="$ROOT/runtime/run/build-boot"
        "$ROOT/runtime/start_c2900_fuzz.sh" start
        "$ROOT/runtime/start_c2900_fuzz.sh" wait "${C2900_BUILD_BOOT_TIMEOUT:-1800}"
    else
        export C2900_FUZZ_START_PAUSED=1
        "$ROOT/runtime/start_c2900_fuzz.sh" start
        "$ROOT/runtime/start_c2900_fuzz.sh" status
    fi
    python3 "$ROOT/runtime/fuzz_client.py" wait --timeout 30
    echo "forkserver IPC / shm 0x2000、0x2001 就绪"
    cleanup_smoke
    trap - EXIT
    echo
    echo "启动脚本 smoke test 通过。"
fi

echo
"$ROOT/scripts/print-config-todo.sh"
