# 2026-10-29 AFLNet 接入真实 C2900 SSH：试跑与差距

## 已跑通

### 1. `-Z` 外部 forkserver 链路（dummy HTTP 对端）

```text
./scripts/verify-aflnet.sh 25  -> PASS
AFLNet: execs=12 bitmap_cvg=2.42% stability=100.00% paths=2
QEMU:   edges=1582; AFLNet 2.42% ≈ 1586 edges
```

说明 AFLNet 2.56b 的 `-Z` 能连接 QEMU forkserver、读 shm 0x2000/0x2001。

### 2. AFLNet 对真实 C2900 SSH（tap1 + M7 + `-P SSH`）

命令（60 s）：

```bash
afl-fuzz -Z -d -i seeds -o out -m none -t 15000 \
  -N tcp://172.16.100.2/22 -P SSH -E -K -q 3 -s 3 \
  -D 1500 -W 2000 -w 100000 -- dummy
```

结果：

```text
execs_done      : 9        （0.15 exec/s，约 6.7 s/轮）
paths_total     : 2        （seed + 1 个 +cov havoc）
bitmap_cvg      : 12.89%
stability       : 100.00%
unique_crashes  : 0        （crash_mode=off）
QEMU 每轮边数   : ~8,355–8,371
```

说明 AFLNet 能真实连接 C2900 SSH、收发包、拿到覆盖率反馈并发现新路径。

## 踩到的坑

- `-w 1000000` 作为 `struct timeval.tv_usec` 非法（必须 < 1,000,000）。
  `SO_RCVTIMEO` 设置失败后 `recv` 永久阻塞，AFLNet 卡死。
  合法值示例：`-w 100000`。
- AFLNet 在阻塞网络读时对 SIGINT 响应慢；停止时用
  `timeout -s INT -k 5 ...` 或外部 kill。
- 启动前 SSH 可能仍在生成 RSA key；未就绪时连接无 banner。必须先真实登录
  确认服务就绪，再让 AFLNet 发 `0x01` 快照。

## 距离正式 campaign 还差

1. **一键 bring-up / readiness 门控**：boot(M7/M9)+tap+等 SSH 登录成功+`0x01`；
   现有 `protocol.sh prepare ssh` 默认 M9/socket，且没有 SSH-ready 检查。
2. **性能调优**：当前 0.15 exec/s。可下调
   `wait_snapshot_restore`/`wait_program_done`（现 1000ms/1000ms）、
   `-W`/`-w`/`-D`，目标多 exec/s；需重跑 dry run 验证不超时。
3. **崩溃判定标定**：正式 campaign 用 `crash_mode=off` 不会发现 crash；
   L0 哨兵不能用于正式 fuzz（每次 SSH 连接都会报 crash）。需按 v1 笔记
   标定真实 panic 地址或 L4/L1 掩码。信号链路本身已验证（shm 0x0b）。
4. **协议纵深**：`-P SSH` 只能解析 NEWKEYS 之前的明文；当前 seed 只覆盖
   KEXINIT 阶段。要覆盖认证/通道需自定义 parser/proxy 或接受 KEX 阶段。
5. **长跑稳定性**：目前只跑 60s/9 轮；需验证长时间 VTY/连接清理、
   快照漂移、路径增长和资源泄漏。
6. **配置与文档固化**：把 production fuzz.json、seed、启动/停止脚本放到
   `runtime/protocols/ssh/`，更新 protocol.env（M7/tap）与本 README。

## 证据

- `logs/ssh-aflnet-20261029/out/fuzzer_stats`
- `logs/ssh-aflnet-20261029/coverage.log`（QEMU 每轮 8.3k 边）
- `logs/ssh-aflnet-20261029/forkserver.log`
- `logs/ssh-aflnet-20261029/out/queue/`（seed + 新路径）
- 真实 SSH 登录/崩溃信号/覆盖率验收：`logs/ssh-verify-20261029/`
