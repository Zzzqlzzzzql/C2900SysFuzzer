# HTTPS campaign

- 配置脚本：`$C2900_PKG/test/config_web.py`
- 快照名：`snap_https`
- 服务端点：`tcp://${GUEST_IP}:443`
- AFLNet `-P`：`TLS`
- 覆盖率配置：`config/fuzz.json`（默认完整 `.text` 范围；标定服务代码后可缩小）
- 日志：`logs/`（console/configure/snapshot/coverage）
- 快照元数据：`snapshots/`
- 种子：`seeds/`（当前需按协议补充）
- AFLNet 输出：`out/`

## 命令

```bash
./scripts/protocol.sh prepare https   # 启动→banner→跑配置脚本→自动 0x01 保存快照
./scripts/protocol.sh status https
./scripts/protocol.sh aflnet-cmd https
./scripts/protocol.sh stop https
```

## 注意

C2900 仅支持 SSLv3+RC4；AFLNet -P TLS 需先验证旧握手，种子需包含真实 SSLv3 握手。

> 快照在 `config_web.py` 成功退出后通过 forkserver `0x01` 保存；
> 快照本身留在 QEMU 进程的临时 overlay 中，停止 QEMU 后失效。
> 需要持久化时使用配置里的 `snapshot_device`（专用 qcow2）。
