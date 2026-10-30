# Telnet campaign

- 配置脚本：`$C2900_PKG/test/config_telnet.py`
- 快照名：`snap_telnet`
- 服务端点：`tcp://${GUEST_IP}:23`
- AFLNet `-P`：`NONE`
- 覆盖率配置：`config/fuzz.json`（默认完整 `.text` 范围；标定服务代码后可缩小）
- 日志：`logs/`（console/configure/snapshot/coverage）
- 快照元数据：`snapshots/`
- 种子：`seeds/`（当前需按协议补充）
- AFLNet 输出：`out/`

## 命令

```bash
./scripts/protocol.sh prepare telnet   # 启动→banner→跑配置脚本→自动 0x01 保存快照
./scripts/protocol.sh status telnet
./scripts/protocol.sh aflnet-cmd telnet
./scripts/protocol.sh stop telnet
```

## 注意

AFLNet 的 -P 列表不含 Telnet；仅保存快照/覆盖率，需要自定义提取器。

> 快照在 `config_telnet.py` 成功退出后通过 forkserver `0x01` 保存；
> 快照本身留在 QEMU 进程的临时 overlay 中，停止 QEMU 后失效。
> 需要持久化时使用配置里的 `snapshot_device`（专用 qcow2）。
