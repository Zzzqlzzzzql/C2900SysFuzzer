# IPsec campaign

- 配置脚本：`$C2900_PKG/test/config_ipsec.py`
- 快照名：`snap_ipsec`
- 服务端点：`udp://${GUEST_IP}:500`
- AFLNet `-P`：`CUSTOM`
- 覆盖率配置：`config/fuzz.json`（默认完整 `.text` 范围；标定服务代码后可缩小）
- 日志：`logs/`（console/configure/snapshot/coverage）
- 快照元数据：`snapshots/`
- 种子：`seeds/`（当前需按协议补充）
- AFLNet 输出：`out/`

## 命令

```bash
./scripts/protocol.sh prepare ipsec   # 启动→banner→跑配置脚本→自动 0x01 保存快照
./scripts/protocol.sh status ipsec
./scripts/protocol.sh aflnet-cmd ipsec
./scripts/protocol.sh stop ipsec
```

## 注意

AFLNet 无 IKE/ESP 的 -P；需自定义 harness。securityk9 license 与 config_ipsec_license.py 需在本次启动前完成并重启。

> 快照在 `config_ipsec.py` 成功退出后通过 forkserver `0x01` 保存；
> 快照本身留在 QEMU 进程的临时 overlay 中，停止 QEMU 后失效。
> 需要持久化时使用配置里的 `snapshot_device`（专用 qcow2）。
