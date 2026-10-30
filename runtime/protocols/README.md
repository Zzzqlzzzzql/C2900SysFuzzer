# 按协议的 fuzz 运行区域

每个子目录对应一个 C2900 协议 campaign。目录之间完全隔离，便于日志、配置、
快照和种子的人工维护。

```text
runtime/protocols/<proto>/
├── protocol.env       # 协议元数据（配置脚本、快照名、端口、AFLNet 参数）
├── configure.py       # 从 vendor/c2900/test/config_*.py 适配的配置脚本
├── config/fuzz.json   # QEMU -fuzz 配置（含 snapshot_name）
├── logs/              # console/configure/snapshot/coverage-final 日志
├── snapshots/         # <snapshot_name>.json 快照元数据
├── seeds/             # AFLNet 种子
└── out/               # AFLNet 输出
```

## 协议入口

| 协议 | configure.py 源自 | 快照名 | 端点 | AFLNet `-P` |
|---|---|---|---|---|
| http | config_web.py | snap_http | tcp/80 | HTTP |
| https | config_web.py | snap_https | tcp/443 | TLS（SSLv3 需验证） |
| ssh | config_ssh.py | snap_ssh | tcp/22 | SSH |
| snmp | config_snmp.py | snap_snmp | udp/161 | SNMP |
| ipsec | config_ipsec.py | snap_ipsec | udp/500 | 无（自定义） |
| telnet | config_telnet.py | snap_telnet | tcp/23 | 无（自定义） |

## 快照时机

`scripts/protocol.sh prepare <proto>`：

1. 启动 QEMU（`-fuzz`）和 `console` 会话；
2. 等待 `Press RETURN to get started!`；
3. 运行本目录的 `configure.py`；
4. **配置脚本退出码为 0 后**，发送 forkserver `0x01`；
5. `snapshots/<snapshot_name>.json` 记录时间、脚本、固件/config hash、覆盖率；
6. QEMU 保持运行，可直接启动 AFLNet。

配置失败时不会保存快照，QEMU 保留运行以便排查；日志在 `logs/`。
`/scripts/protocol.sh configure <proto>` 可以对已经运行的实例重跑配置并重新快照。

## 超时

插桩 QEMU 很慢，适配版 `configure.py` 将 vendor 脚本的 30/60/120s 超时改为：

```text
C2900_CONFIG_PROMPT_TIMEOUT=300
C2900_CONFIG_TEXT_TIMEOUT=600
C2900_CONFIG_RSA_TIMEOUT=900
```

shell 层 `CONFIG_TIMEOUT=1800`。可在 `protocol.env` 中覆盖。

## 新增协议

1. 复制一个协议目录；
2. 修改 `protocol.env`：`PROTO_NAME`、`CONFIG_SCRIPT_REL`、`SNAPSHOT_NAME`、
   `AFLNET_PROTOCOL`、`NET_TRANSPORT`、`NET_PORT`；
3. 修改 `config/fuzz.json` 的 `snapshot_name`（可选调整 coverage 范围）；
4. 放入种子；如为特殊协议（IPsec/Telnet），准备自定义 harness；
5. `scripts/protocol.sh list` 应能看到新协议。

## 注意

- vendor 配置脚本固定使用 tmux 会话 `console`；同一时间只运行一个 prepare；
- prepare 保存快照后立即启动 AFLNet；AFLNet 启动时会再发一次 `0x01`，中间
  不要手动改动 guest 状态；
- 快照在 QEMU 进程的临时 overlay 中，停止 QEMU 后失效；持久化请使用
  `snapshot_device`（专用 qcow2）；
- 当前 `NET_MODE=socket`，宿主没有到 guest 的 TCP/UDP 路径；接入 AFLNet 前
  需要改用 `-nic user`（libslirp）或 tap；
- HTTPS 只支持 SSLv3/RC4；SSH 使用旧 KEX/cipher；IPsec 无 stock AFLNet `-P`。
