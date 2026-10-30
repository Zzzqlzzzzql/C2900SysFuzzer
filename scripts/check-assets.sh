#!/usr/bin/env bash
# 校验运行 C2900 原型所需的私有资产（固件、设备库、运行库、AFLNet）。
#
# 私有资产清单与获取方式见 docs/PRIVATE-ASSETS.md。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="${C2900_PKG:-$ROOT/vendor/c2900}"
QEMU="${C2900_QEMU:-$ROOT/vendor/qemu/bin/qemu-system-mips64}"
QEMU_LIB="${C2900_QEMU_LIB:-$ROOT/vendor/qemu/lib}"
export LD_LIBRARY_PATH="$QEMU_LIB:$PKG/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
QUIET=0
[ "${1:-}" = "-q" ] && QUIET=1

fail=0
ok()   { [ "$QUIET" -eq 1 ] || printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*" >&2; }
bad()  { printf '  [FAIL] %s\n' "$*" >&2; fail=1; }
info() { [ "$QUIET" -eq 1 ] || printf '  [info] %s\n' "$*"; }

echo "== 固件/磁盘（C2900_PKG=$PKG） =="
if [ -f "$PKG/firmware/checksums.sha256" ]; then
    while read -r expected name; do
        case "$expected" in ''|'#'*) continue ;; esac
        f="$PKG/firmware/$name"
        if [ ! -f "$f" ]; then
            bad "缺少 firmware/$name"
            continue
        fi
        actual="$(sha256sum "$f" | awk '{print $1}')"
        if [ "$actual" = "$expected" ]; then
            ok "firmware/$name"
        else
            case "$name" in
                disk0.raw|flash.bin|rommon_var.bin)
                    warn "firmware/$name 哈希已变化（运行时写入，属正常）" ;;
                *)
                    bad "firmware/$name 哈希不匹配（期望 ${expected:0:12}…，实际 ${actual:0:12}…）" ;;
            esac
        fi
    done < "$PKG/firmware/checksums.sha256"
else
    bad "缺少 $PKG/firmware/checksums.sha256"
fi

echo "== QEMU 设备库/二进制 =="
MACH_HASH="a49f47f6fe302c063231f195cb4abd52684cc7f81c7cd1dbb6edad572b0e980f"
if [ -f "$QEMU_LIB/libmachcore.so" ]; then
    if echo "$MACH_HASH  $QEMU_LIB/libmachcore.so" | sha256sum -c --quiet >/dev/null 2>&1; then
        ok "libmachcore.so（v3，TX 已修复）"
    else
        bad "libmachcore.so 哈希不匹配（需要 v3 / BUILD_ID EVAL-20261028-F）"
    fi
else
    bad "缺少 $QEMU_LIB/libmachcore.so"
fi
if [ -x "$QEMU" ]; then
    ok "QEMU: $QEMU"
    if "$QEMU" --help 2>&1 | grep -q -- "-fuzz"; then
        ok "QEMU 含 -fuzz（插桩已生效）"
    else
        bad "QEMU 不含 -fuzz（重新构建或替换为插桩版本）"
    fi
else
    warn "未找到 QEMU：$QEMU（可用 scripts/build-qemu.sh 构建）"
fi

echo "== 运行库 =="
if [ -d "$PKG/lib" ] && [ -n "$(ls -A "$PKG/lib" 2>/dev/null | grep -v '^.gitkeep$')" ]; then
    ok "运行库目录：$PKG/lib"
else
    warn "未找到 $PKG/lib；若系统库版本兼容可忽略，否则从私有资产包复制"
fi

echo "== AFLNet（可选，仅 campaign/verify-aflnet 需要） =="
AFL="${AFL_FUZZ:-$ROOT/vendor/aflnet/afl-fuzz}"
if [ -x "$AFL" ]; then
    ok "AFLNet: $AFL"
elif [ -f "$ROOT/vendor/aflnet/Makefile" ]; then
    warn "AFLNet 子模块已初始化但未编译：make -C vendor/aflnet -j"
else
    warn "AFLNet 子模块未初始化：git submodule update --init --recursive"
fi

echo
if [ "$fail" -eq 0 ]; then
    [ "$QUIET" -eq 1 ] || echo "私有资产检查通过。"
else
    echo "私有资产检查失败；完整清单见 docs/PRIVATE-ASSETS.md。" >&2
fi
exit "$fail"
