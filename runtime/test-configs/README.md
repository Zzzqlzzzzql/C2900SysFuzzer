# 测试配置（崩溃判定/覆盖率验证）

这些配置只用于验证反馈链路，**不要用于正式 fuzz**。

| 文件 | 用途 |
|---|---|
| `crash-sentinel-m7.json` | L0 哨兵 `0x81000000`，`crash_mode=on`，`crash_status=11` |
| `crash-sentinel-m9.json` | 同上，M9 `.text` 范围 |
| `crash-log-m7.json` | L0 哨兵，`crash_mode=log`（只记录，不改 shm） |
| `crash-exception-log-m7.json` | L4 异常掩码 `[2,3]`（TLB），log 模式 |
| `crash-exception-int-m7.json` | L4 异常掩码 `[0]`（外部中断），链路测试 |

用法见项目 README §7.5 与
`（内部笔记，未公开）`。

```bash
cd <仓库根目录>
./scripts/verify-crash.sh 4
```

正式协议配置（`runtime/protocols/*/config/fuzz.json`）默认 `crash_mode=off`；
真实 IOS 崩溃标定后再改为 `log`/`on`。
