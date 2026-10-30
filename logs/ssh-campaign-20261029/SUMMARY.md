# 2026-10-29 正式 SSH campaign 编排验收

## 流程

```bash
cd runtime/protocols/ssh
./campaign.sh start
```

- 默认 M7 + `tap1`；等待 banner（约 26 分钟，插桩 + `-icount shift=1`）。
- `ssh_ready.py` 做真实 Paramiko 登录（`admin/cisco`，banner
  `SSH-1.99-Cisco-1.25`）；首次未就绪时 console nudge 触发 RSA key 生成。
- 确认 `show ssh` 无活动会话后 `0x01` 保存快照 `snap_ssh_aflnet`：
  status=1，bitmap edges=63,312。
- 生产配置 `config/fuzz.aflnet-m7.json`：`crash_mode=off`、全 `.text`。

随后：

```bash
./campaign.sh aflnet
```

## AFLNet 结果（真实 C2900 SSH）

```text
dry run: len=205, map size=8383, exec speed≈6.68s
execs_done      : 9
execs_per_sec   : 0.15
paths_total     : 1
bitmap_cvg      : 13.48%
stability       : 100.00%
unique_crashes  : 0   (crash_mode=off)
```

QEMU 每轮边覆盖约 6,537–8,383；AFLNet bitmap 与 QEMU shm 对得上。
说明 **start → SSH-ready → snapshot → AFLNet 端到端已闭环**。

## 已知事项

- QEMU 的 shm 0x2000 在 `assert_shm_ok` 时可能带 `(deleted)` 标记；
  项目自带 `verify-aflnet` 同样如此，AFLNet 仍可按 key attach，故脚本
  只告警不失败。
- 0.15 exec/s（~6.7s/轮），本轮按要求未做性能调优；固定等待仍为
  `wait_snapshot_restore=1000ms`、`wait_program_done=1000ms`、
  AFLNet `-W 2000 -w 100000 -D 1500`。
- `-P SSH` 只覆盖 NEWKEYS 前的明文阶段；认证/通道阶段需自定义
  parser/proxy。
- `crash_mode=off`，本轮仅验证覆盖率与框架；真实 crash 需另行标定
  L0/L4/L1，不能使用 SSH 哨兵配置。

## 证据

- `campaign.log`：start/ready/snapshot/AFLNet 输出
- `coverage.log`：QEMU 每轮边覆盖
- `forkserver.log`：0x01/0x02/0x03 与 vm 状态
- `readiness.log`：SSH 登录探测
- `out/fuzzer_stats`、`out/queue/`
- 真实 SSH/崩溃/覆盖率前置验收：`logs/ssh-verify-20261029/`
