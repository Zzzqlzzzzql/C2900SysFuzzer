# 验收记录摘要

完整日志见 [`../logs/`](../logs/)。以下为当前（2026-10-29）结论。

## 1. QEMU 插桩 + 快照协议（M7，v3）

| 项 | 结果 |
|---|---|
| 启动到 IOS banner（插桩 + `-icount shift=1`） | 约 23 min |
| `0x01` 保存快照 | status=1，baseline edge 62,811 |
| `0x02` 恢复快照 | status=1，约 1.25–1.41 s；覆盖率重置到 86 边 |
| `0x03` 结束本轮（无输入） | status=1，exit status `0xdeadbeef` |
| 连续 3 轮 ARP 输入 | edge 5,868 / 5,870 / 5,869；guest 每轮回 ARP |

证据：`logs/v3-fuzz-acceptance-20261029/`。

## 2. 真实 SSH + AFLNet campaign

- `tap1` + Paramiko 完成真实登录（`SSH-1.99-Cisco-1.25`，admin/cisco）。
- 无输入基线 132 边；真实 SSH 轮 13,115 / 13,784 边。
- L0 SSH 哨兵使 shm `0x2001=0x0b`；`0x02` 后恢复 `0xdeadbeef`。
- AFLNet `-Z` 60 s 试跑：9 execs、bitmap_cvg 13.48%、stability 100%、
  paths_total 1、unique_crashes 0。

证据：`logs/ssh-verify-20261029/`、`logs/ssh-campaign-20261029/`、
`logs/ssh-aflnet-20261029/`。

## 3. 早期覆盖率真实性验证

- paused edges=0；cont 后 1544；stop 后 hash 两次相同；cont 后变化。
- mid-boot 快照 1544 edges；`0x02` reset 后 20 edges。
- AFLNet `bitmap_cvg=2.36%` ≈ 1547 边，与 QEMU shm 一致。

证据：`logs/verification-2026-10-19/`。

## 4. 仍未完成

- 真实 IOS 崩溃地址/异常掩码标定（当前 `crash_mode=off`）。
- campaign 性能调优与长跑。
- 崩溃判定 v1 只用合成哨兵验证过（L0/L4/L1 → shm `0x2001`）。
