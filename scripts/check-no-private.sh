#!/usr/bin/env bash
# 防误提交检查：私有资产路径、过大文件。
# 建议在 push 前执行，或作为 pre-commit hook。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAX=$((5 * 1024 * 1024))
fail=0

cd "$ROOT"
echo "== 私有路径检查 =="
while IFS= read -r -d '' f; do
    case "$f" in
        vendor/*private-assets*/|vendor/*private-assets*.tar.zst|vendor/*private-assets*.tar.gz|\
        vendor/*private-assets*.sha256|vendor/.private.*|\
        vendor/qemu/src/*|vendor/qemu/*.tar.gz|vendor/qemu/*.tar.zst|\
        vendor/qemu/*.tar.xz|vendor/qemu/*.zip|\
        vendor/qemu/bin/*|vendor/qemu/lib/*|\
        vendor/c2900/firmware/*|vendor/c2900/lib/*|vendor/c2900/bin/*|\
        vendor/c2900/logs/*|vendor/aflnet/*)
            case "$f" in
                vendor/qemu/bin/.gitkeep|vendor/qemu/lib/.gitkeep|\
                vendor/c2900/lib/.gitkeep|\
                vendor/c2900/firmware/checksums.sha256|\
                vendor/c2900/firmware/README.md)
                    ;;
                *)
                    echo "  [FAIL] 私有路径被跟踪：$f" >&2
                    fail=1
                    ;;
            esac
            ;;
    esac
done < <(git ls-files -z)

if [ "$fail" -eq 0 ]; then
    echo "  未发现私有路径。"
fi

echo "== 大文件检查（> ${MAX} B） =="
while IFS= read -r -d '' f; do
    [ -f "$f" ] || continue
    size=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt "$MAX" ]; then
        echo "  [FAIL] $((size / 1024 / 1024)) MB  $f" >&2
        fail=1
    fi
done < <(git ls-files -z)

if [ "$fail" -eq 0 ]; then
    echo "  未发现超过 5 MB 的文件。"
fi

exit "$fail"
