#!/usr/bin/env bash
# 打印构建完成后需要人工确认/填写的配置位置与当前默认值。
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

get() { sed -n "s/^$1=//p" "$2" | head -1; }

cat <<'EOF'
================ 需要手动确认 / 填写的配置 ================

以下列出的是当前默认值；若与你的实验环境不同，请修改对应文件。
使用默认的 tap1 / 172.16.100.0/24 网络时，通常无需改动。

EOF

echo "1) 固件与 fuzz 主配置"
echo "   runtime/c2900-ios-m7.json   # M7 C2900-UN.150.BIN"
echo "   runtime/c2900-ios.json      # M9 外层镜像"
for f in runtime/c2900-ios-m7.json runtime/c2900-ios.json; do
    [ -f "$f" ] || continue
    printf '   %s: coverage_start=%s coverage_end=%s text_start=%s snapshot_name=%s crash_mode=%s\n' \
        "$f" \
        "$(grep -o '"coverage_start"[^,]*' "$f" | cut -d'"' -f4)" \
        "$(grep -o '"coverage_end"[^,]*' "$f" | cut -d'"' -f4)" \
        "$(grep -o '"text_start"[^,]*' "$f" | cut -d'"' -f4)" \
        "$(grep -o '"snapshot_name"[^,]*' "$f" 2>/dev/null | cut -d'"' -f4)" \
        "$(grep -o '"crash_mode"[^,]*' "$f" | cut -d'"' -f4)"
done

echo
echo "2) 协议入口 / campaign 默认值"
for env in runtime/protocols/*/protocol.env; do
    [ -f "$env" ] || continue
    proto="$(basename "$(dirname "$env")")"
    printf '   [%s] %s\n' "$proto" "$env"
    printf '        GUEST_IP=%s  NET_PORT=%s  NET_TRANSPORT=%s  SNAPSHOT_NAME=%s  KERNEL_REL=%s\n' \
        "$(get GUEST_IP "$env")" "$(get NET_PORT "$env")" \
        "$(get NET_TRANSPORT "$env")" "$(get SNAPSHOT_NAME "$env")" \
        "$(get KERNEL_REL "$env")"
done

echo
echo "3) 协议 fuzz JSON（snapshot_name / crash_mode / 覆盖率范围）"
for f in runtime/protocols/*/config/fuzz*.json; do
    [ -f "$f" ] || continue
    printf '   %s\n' "$f"
    printf '        snapshot_name=%s  crash_mode=%s  coverage=%s..%s\n' \
        "$(grep -o '"snapshot_name"[^,]*' "$f" | cut -d'"' -f4)" \
        "$(grep -o '"crash_mode"[^,]*' "$f" | cut -d'"' -f4)" \
        "$(grep -o '"coverage_start"[^,]*' "$f" | cut -d'"' -f4)" \
        "$(grep -o '"coverage_end"[^,]*' "$f" | cut -d'"' -f4)"
done

cat <<'EOF'

4) 网络（真实协议 campaign 必填/必确认）
   - 宿主机先建 tap：sudo ./scripts/setup-tap.sh tap1
   - 默认 TAP_IFACE=tap1，HOST_IP=172.16.100.1，GUEST_IP=172.16.100.2
   - 可在 campaign.sh 前覆盖：KIND=m7|m9 TAP_IFACE=... HOST_IP=... GUEST_IP=...
   - 基础 fuzz（socket 网卡）不需要 tap/sudo

5) 登录/服务参数（按协议）
   - SSH/Telnet/Web 默认 admin / cisco
   - SSH 使用旧 KEX/cipher；HTTPS 需先 clock set（config_web.py 已处理）
   - SSH campaign 凭据在 campaign.sh 顶部：SSH_USER / SSH_PASS / ENABLE_PASSWORD

6) 选择固件
   - campaign.sh：KIND=m7（C2900-UN.150.BIN）或 KIND=m9（外层 M9 镜像）
   - 不用 campaign、直接 start 时：C2900_KERNEL=... 或修改 runtime/c2900-ios*.json 对应启动命令

=========================================================

下一步启动（默认 M9、socket 网卡）：
  ./runtime/start_c2900_fuzz.sh start
  ./runtime/start_c2900_fuzz.sh wait 1800
  python3 runtime/fuzz_client.py save
  python3 runtime/fuzz_client.py round
  python3 runtime/fuzz_client.py end

协议 campaign（以 SSH 为例）：
  sudo ./scripts/setup-tap.sh tap1
  cd runtime/protocols/ssh
  KIND=m7 TAP_IFACE=tap1 ./campaign.sh start
  ./campaign.sh aflnet

插桩启动到 banner 约 20 分钟，属正常。
EOF
