#!/usr/bin/env bash
# 准备子模块 + AFLNet + 私有资产 + 环境检查。
# 说明：完整一键构建（含 QEMU 构建和启动 smoke test）请用根目录 ./build.sh；
# 本脚本保留给“只准备资产/工具链”的场景。
#
# 用法：
#   ./scripts/bootstrap.sh [private-assets-dir] [prepare 参数...]
#
# 例：
#   ./scripts/bootstrap.sh ~/c2900-private-assets --link
#   ./scripts/bootstrap.sh                 # 子模块 + AFLNet；若项目内放了
#                                          # vendor/qemu/qemu-8.2-eval-v3.tar.gz 则自动解包
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS=""
if [ $# -ge 1 ] && [ "${1#-}" = "$1" ]; then
    ASSETS="$1"
    shift
fi

echo "== AFLNet =="
"$ROOT/scripts/prepare-aflnet.sh"

if [ -n "$ASSETS" ]; then
    echo "== 私有资产 =="
    "$ROOT/scripts/prepare-private-assets.sh" "$ASSETS" "$@"
elif [ ! -f "$ROOT/vendor/qemu/src/VERSION" ] && \
     [ -f "$ROOT/vendor/qemu/qemu-8.2-eval-v3.tar.gz" ]; then
    echo "== QEMU 源码（项目内放置） =="
    "$ROOT/scripts/prepare-qemu-src.sh"
fi

echo "== 覆盖率插件（可选） =="
if [ -f "$ROOT/vendor/qemu/src/include/qemu/module.h" ]; then
    "$ROOT/scripts/build-coverage-plugin.sh" || \
        echo "警告：libcoverage2.so 构建失败；不影响主 fuzz 流程" >&2
else
    echo "未找到 vendor/qemu/src，跳过插件构建（需要 QEMU 源码头文件）"
fi

echo "== 检查 =="
"$ROOT/scripts/check-assets.sh" || true
"$ROOT/scripts/check-env.sh" || true
