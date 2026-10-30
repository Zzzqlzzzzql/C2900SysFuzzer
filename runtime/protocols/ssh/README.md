# SSH campaign

> **2026-10-29 状态：真实 SSH 已验收。**
> `tap1`（宿主 172.16.100.1/24）↔ guest Gi0/0（172.16.100.2/24）双向可用；
> Paramiko 3.4.1 用 Cisco 旧算法完成真实登录（`admin/cisco`，
> banner `SSH-1.99-Cisco-1.25`）。无输入基线 132 边；真实 SSH 轮
> 13,115 / 13,784 边，位图相对基线新增 12,984 边。
> L0 SSH 哨兵命中后 shm `0x2001=0x0b`，`0x02` 后恢复 `0xdeadbeef`。
> 证据：`logs/ssh-verify-20261029/`。
>
> v3 已修复 guest→host TX；10-19 的 `soc-eval` 无网口阻塞不再适用。
> 旧的降级模式 `C2900_SSH_SKIP_INTERFACE=1` 仅保留用于无网络链路验证。

- 固件静态分析（不启动 QEMU）：
  `（内部笔记，未公开）`
  与 `config/firmware-analysis.json`（详细报告已随项目 docs 备份）
  - 协议定义/端口：TCP 22，handler `SSH-Server`，protocol-id 40（端口固定，无 `ip ssh port`）
  - 建议入口：M9 SSH2 报文循环 `0x8383e3d8`；完整覆盖范围
    phys `0x0382b834-0x0384939c`（M7 `0x03826fd4-0x03844b70`）
  - 作用域配置：`config/fuzz.ssh-scoped-m9.json` / `fuzz.ssh-scoped-m7.json`
  - 机器可读汇总：`config/firmware-analysis.json`
  - 证据：`analysis/`（反汇编、字符串、协议定义表、probe JSON）
  - 明文握手种子：`seeds/ssh_kexinit.raw`（`scripts/gen_ssh_seed.py` 生成）
- 配置脚本：`runtime/protocols/ssh/configure.py`（源自
  `vendor/c2900/test/config_ssh.py`，增加超时并设置 Gi0/0 IP）
- 快照名：`snap_ssh`
- 服务端点：`tcp://172.16.100.2:22`
- AFLNet `-P`：`SSH`
- 覆盖率配置：`config/fuzz.json`
- 日志：`logs/`、`Logs/`
- 快照元数据：`snapshots/snap_ssh.json`
- 种子：`seeds/`（已含生成的明文握手 `ssh_kexinit.raw`；深入认证建议再抓真实会话）
- AFLNet 输出：`out/`

## 完整流程

### 0. 准备宿主网络

默认 `NET_MODE=socket` 只跑 boot/覆盖率，AFLNet 无法直接到 guest。SSH fuzz
需要 tap（推荐）或 QEMU user 网络：

```bash
# 需要一个独立的 tap；不要动正在被其他项目使用的 tap0。
# 本机 tap1 已持久化（persist on, user Zql, 172.16.100.1/24）：
# QEMU 以当前用户附着即可，实测无需 sudo，carrier 会自动 UP。
# 新建 tap 时才需要（用户在终端执行）：
#   sudo ./scripts/setup-tap.sh tap1
```

然后：

```bash
NET_MODE=tap TAP_IFACE=tap1 ./scripts/protocol.sh prepare ssh
```

2026-10-29 验收使用的等价直接启动参数：

```bash
C2900_NIC_ARGS="tap,model=octeon-eth,ifname=tap1,script=no,downscript=no,mac=52:54:00:12:34:56"
```

（`user` 模式需要 QEMU 编入 libslirp，当前 vendor 预编译二进制没有。
本机 tap0 已被 peach-fuzz 的 QEMU 占用，所以用 tap1。）

### 1. 准备 SSH 快照

```bash
cd <仓库根目录>
./scripts/protocol.sh prepare ssh
```

内部流程：

1. 启动 QEMU（M9 默认）+ tap + `console`；
2. 等 `Press RETURN to get started!`（约 11.5 min；M7 约 8 min）；
3. 运行 `configure.py`：切到配置模式，设置 hostname/domain、**Gi0/0
   `172.16.100.2/24` + no shutdown**、生成 RSA key、创建
   `admin/cisco`、VTY `transport input ssh`；
4. 配置脚本退出码 0 后，发送 `0x01` 保存 `snap_ssh`；
5. 写 `snapshots/snap_ssh.json`，QEMU 保持运行。

### 2. 验证服务

```bash
ping -c 2 172.16.100.2
nc -vz 172.16.100.2 22
ssh-keygen -R 172.16.100.2
ssh -o KexAlgorithms=+diffie-hellman-group1-sha1 \
    -o HostKeyAlgorithms=+ssh-rsa -o Ciphers=+aes128-cbc \
    admin@172.16.100.2     # 密码 cisco
```

> 手工 SSH 登录会改变 guest 状态。若已验证过，请在启动 AFLNet 前重新
> `./scripts/protocol.sh configure ssh` 或在 monitor 里 `loadvm snap_ssh`，
> 保证 AFLNet 的启动 `0x01` 保存的是“无活动连接”的服务就绪状态。

### 3. 准备 SSH 种子

`seeds/` 已包含一个按固件算法生成的明文握手种子：
`ssh_kexinit.raw`（`scripts/gen_ssh_seed.py` 生成，客户端版本行 +
SSH_MSG_KEXINIT，算法与服务端支持列表有交集）。它可用于 AFLNet
`-P SSH` 的 dry run/初始变异。

要覆盖认证/通道状态，仍需客户端方向真实 SSH 字节流（NEWKEYS 之后加密）。
例如在 tap 上抓包后提取客户端→服务器 payload：

```bash
sudo tcpdump -i tap0 -w /tmp/ssh.pcap 'tcp port 22'
# 另开终端执行上面的 ssh 登录
# 用 Wireshark Follow TCP Stream (Raw) 或 tcpflow 提取客户端字节流
# 保存为 runtime/protocols/ssh/seeds/ssh_login.raw
```

没有有效种子时 AFLNet 会在 dry run 失败。

### 4. 启动 AFLNet

```bash
./scripts/protocol.sh aflnet-cmd ssh
# 输出的命令类似：
# cd runtime/protocols/ssh && vendor/aflnet/afl-fuzz -Z -d \
#   -i seeds -o out -m none -t 10000 \
#   -N tcp://172.16.100.2/22 -P SSH -E -K -q 3 -s 3 \
#   -D 1500 -W 500 -- dummy
```

建议在独立 tmux 会话里运行。AFLNet 启动时会发 `0x01`，所以必须在本目录
`prepare`/`configure` 之后、guest 仍处于 SSH 就绪状态时启动。

### 5. 观测

```bash
tail -f out/fuzzer_stats        # execs_done/bitmap_cvg/stability/paths_total
tail -f logs/console.log
tail -f Logs/coverage.log
../../coverage_probe.py stats
```

### 6. 停止

```bash
# 先 Ctrl-C 结束 afl-fuzz
./scripts/protocol.sh stop ssh
sudo <仓库根目录>/scripts/teardown-tap.sh tap1   # 如果不再使用 tap1
```

### 7. 2026-10-29 真实 SSH 验收（已跑通）

环境：v3 + overlay、M7、`tap1`、`-fuzz`、`crash_mode=on`，全 `.text`
覆盖率；快照在 SSH 服务就绪、`show ssh` 无活动会话时保存。

```text
基线（无输入）: 0x02 -> 132 边; 0x03 -> exit=0xdeadbeef
SSH 轮 1:       Paramiko 登录 admin/cisco + show clock
                0x03 -> 13,115 边; exit=0x0000000b (signal 11)
SSH 轮 2:       登录 + show ip interface brief
                0x03 -> 13,784 边; exit=0x0000000b
位图对比:       baseline 132 / SSH 13,116，新增 12,984，公共 132
```

L0 哨兵命中：`0x238328b4`(accept)、`0x238329f0`(listener)、
`0x2382c5fc`(ssh_receive)、`0x238398a8`(SSH2 loop)、`0x23839458`(parser)。
这是**信号链路验证**，不是真实漏洞：哨兵地址是人为配置的 SSH 代码地址，
命中即按 `crash_status=11` 写 shm `0x2001`；`0x02` 会重置回 `0xdeadbeef`。

真实客户端脚本：`runtime/protocols/ssh/tools/ssh_real.py`
（Paramiko 3.4.1，强制 group14-sha1 / aes128-cbc / hmac-sha1 / ssh-rsa）。
证据与完整日志：`logs/ssh-verify-20261029/`。

## 注意

- `configure.py` 会把 VTY 从 Telnet 切到 SSH；C2900 使用旧 KEX/cipher
  （`diffie-hellman-group14-sha1`/`group1-sha1`、`ssh-rsa`、`aes128-cbc`）。
  2026-10-29 已用 Paramiko 完成真实登录；AFLNet `-P SSH` 解析器仍需接种子验证。
- 快照保存在 QEMU 进程的临时 overlay 中，`stop` 后失效；需要持久化时使用
  `snapshot_device`（专用 qcow2）。
- 覆盖率范围目前是完整 `.text`；要计算 SSH 服务 80% 覆盖率需另行标定
  服务代码范围/累计 block 覆盖率。10-29 的位图差分已证明 SSH 流量带来的
  新增边（12,984），但尚未按 SSH 作用域范围单独统计。
- SSH 首次可用前 IOS 可能仍在生成 RSA key（串口出现
  `Generating 1024 bit RSA keys`）；此期间连接会被关闭且无 banner。
  应先用 `show ip ssh` 和一次真实登录确认服务就绪，再执行 `0x01`。

## 8. 2026-10-29 AFLNet 试跑状态

- `-Z` 链路：`scripts/verify-aflnet.sh 25` PASS（execs=12、bitmap_cvg 2.42%、
  stability 100%，QEMU edges 与 AFLNet bitmap 一致）。
- 真实 C2900 SSH：AFLNet `-P SSH` 60 s 试跑，execs=9、paths_total=2、
  bitmap_cvg=12.89%、stability=100%、unique_crashes=0（crash_mode=off）。
  约 0.15 exec/s。
- 参数坑：`-w` 必须是合法 `tv_usec`（< 1,000,000）；`-w 1000000` 会导致
  `SO_RCVTIMEO` 设置失败、`recv` 永久阻塞。建议 `-w 100000 -W 2000` 起步。
- 正式 campaign 前还需：SSH-ready 自动门控、等待时间/性能调优、
  真实崩溃标定（不能用合成 L0 哨兵）、长跑稳定性验证。详见
  `logs/ssh-aflnet-20261029/SUMMARY.md`。

## 9. 正式 campaign 启动/快照编排（campaign.sh）

```bash
cd runtime/protocols/ssh
./campaign.sh start     # tap 启动 → banner → SSH 真实登录就绪 → 0x01 快照
./campaign.sh aflnet    # 对运行中的实例启动 AFLNet -Z -P SSH（前台）
./campaign.sh status    # QEMU/forkserver/覆盖率和 fuzzer_stats
./campaign.sh stop      # 停止 QEMU 与 console
```

- 默认 `KIND=m7`、`TAP_IFACE=tap1`、`SSH_USER=admin`、`SSH_PASS=cisco`；
  M9 用 `KIND=m9`。
- `start` 会等待 banner，然后用 `ssh_ready.py` 做真实 Paramiko 登录；
  若 SSH 未就绪会通过 console nudge 一次（触发 RSA key 生成），并确认
  `show ssh` 无活动会话后才保存快照。
- 生产配置：`config/fuzz.aflnet-{m7,m9}.json`，`crash_mode=off`；
  正式找 crash 前需另行标定真实崩溃地址，不能使用 SSH 哨兵配置。
- 种子：`seeds-aflnet/`（只放 AFLNet 种子文件，不放 README）。
- `aflnet` 启动前会检查 QEMU 的 shm `0x2000` 未被标记删除；若失败，
  先 `stop` 再 `start`。默认输出目录 `out-aflnet/`，非空时需
  `AFL_RESUME=1` 或换 `AFL_OUT`。
- 2026-10-29 已用该流程完成 start/ready/snapshot；AFLNet 真实 SSH 试跑
  见 `logs/ssh-aflnet-20261029/`。
