#!/usr/bin/env bash
# 便捷入口：等价于 runtime/start_c2900_fuzz.sh
#
#   ./start.sh start | stop | status | wait [seconds]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/runtime/start_c2900_fuzz.sh" "$@"
