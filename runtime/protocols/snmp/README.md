# SNMP campaign

- 配置脚本：`$C2900_PKG/test/config_snmp.py`
- 快照名：`snap_snmp`
- 服务端点：`udp://${GUEST_IP}:161`
- AFLNet `-P`：`SNMP`
- 覆盖率配置：`config/fuzz.json`（默认完整 `.text` 范围；标定服务代码后可缩小）
- 日志：`logs/`（console/configure/snapshot/coverage）
- 快照元数据：`snapshots/`
- 种子：`seeds/`（当前需按协议补充）
- AFLNet 输出：`out/`

## 命令

```bash
./scripts/protocol.sh prepare snmp   # 启动→banner→跑配置脚本→自动 0x01 保存快照
./scripts/protocol.sh status snmp
./scripts/protocol.sh aflnet-cmd snmp
./scripts/protocol.sh stop snmp
```

## 注意

AFLNet -P SNMP；UDP 目标。

> 快照在 `config_snmp.py` 成功退出后通过 forkserver `0x01` 保存；
> 快照本身留在 QEMU 进程的临时 overlay 中，停止 QEMU 后失效。
> 需要持久化时使用配置里的 `snapshot_device`（专用 qcow2）。
