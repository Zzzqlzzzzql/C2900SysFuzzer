# 2026-10-29 真实 SSH 环境 + 崩溃信号 + 覆盖率验收

## 结论

- **真实 SSH 环境成立**：宿主经 `tap1`（172.16.100.1/24）与 guest
  Gi0/0（172.16.100.2/24）互通；`SSH-1.99-Cisco-1.25` 服务可用，
  Paramiko 3.4.1 使用 Cisco 旧算法完成真实登录，`admin/cisco` 执行
  `show clock` / `show ip interface brief` 成功。
- **崩溃信号传递成立**：配置 L0 SSH 代码哨兵后，真实 SSH 连接/报文处理
  命中哨兵，shm `0x2001` 从 `0xdeadbeef` 变为 **`0x0000000b`**
  （waitpid SIGSEGV），`0x03` 输出 `exit status: 0x0000000b (signal 11)`；
  `0x02` 后恢复 `0xdeadbeef`，基线轮不误报。
- **覆盖率真实拿到**：无输入基线 **132 边**；真实 SSH 登录轮
  **13,115 / 13,784 边**；位图对比显示 SSH 轮相对基线新增 **12,984 条边**，
  且两轮 SSH 间有 13,001 条公共边，说明覆盖来自 guest SSH 实际执行。

## 环境与配置

| 项 | 值 |
|---|---|
| QEMU | v3 + overlay，`-fuzz`，M7 `C2900-UN.150.BIN` |
| 网络 | `tap1`，宿主 172.16.100.1/24，QEMU 以用户 Zql 直接附着（无需 sudo） |
| guest | Gi0/0 172.16.100.2/24；SSH 1.99，RSA 1024，VTY `transport input ssh` / `login local` |
| fuzz 配置 | `fuzz.json`（全 `.text`，`exclude_baseline=true`，`crash_mode=on`） |
| L0 哨兵 | 7 个 SSH 相关物理地址，见下 |
| 快照 | `snap_ssh_verify`，在 SSH 服务就绪、无活动会话时 `0x01` |

L0 哨兵（`panic_addrs`，phys）：

```text
0x038328b4  accept / session setup
0x038329f0  TCP listener callback
0x0382c5fc  ssh_receive
0x03830398  SSHv1 data entry
0x03839458  SSH2 packet parser
0x038398a8  SSH2 packet loop / service_entry
0x03839fcc  SSH2 message dispatcher
```

## 实测数据

### 快照与基线（无输入）

```text
0x01 save snapshot -> status=1, bitmap edges=63,890
0x02 loadvm       -> status=1, 1.53 s, edges 63,890 -> 132
0x03 end round    -> status=1, edges=132, exit=0xdeadbeef
```

注：保存快照前做过真实 SSH 预检，shm 当时仍是预检留下的 `0x0b`；
第一个 `0x02` 基线轮把它重置为 `0xdeadbeef`，且该轮无 SSH 输入、无崩溃。

### SSH 轮 1（真实登录，`show clock`）

```text
0x02 loadvm       -> status=1, 1.23 s, edges 132
Paramiko          -> SSH-1.99-Cisco-1.25, auth OK, show clock OK
0x03 end round    -> status=1, edges=13,110, exit=0x0000000b
coverage.log      -> edge coverage: 13,115; exit status: 0x0000000b (signal 11)
bitmap diff       -> baseline 132 / ssh 13,116; only in ssh 12,984; common 132
```

### SSH 轮 2（真实登录，`show ip interface brief`）

```text
0x02 loadvm       -> status=1, 1.23 s, edges 13,446 -> 131
reset 后 probe    -> exit=0xdeadbeef（确认 0x02 清崩溃状态）
Paramiko          -> banner/auth OK, show ip interface brief 输出正常
0x03 end round    -> status=1, edges=13,784, exit=0x0000000b
round1 vs round2  -> common 13,001; only R1 115; only R2 783
```

### 崩溃候选命中地址

```
pc=0x238328b4 accept
pc=0x238329f0 listener
pc=0x2382c5fc ssh_receive
pc=0x238398a8 SSH2 packet loop
pc=0x23839458 SSH2 packet parser
```

说明信号来自真实 SSH 报文处理路径，而不仅是 TCP 建连；`0x02` 会把
`0x2001` 重置为 `0xdeadbeef`，基线轮（无连接）不触发。

## 重要 caveat

本轮是**崩溃信号链路验证**，不是发现真实 IOS 漏洞：
L0 哨兵是人为挑选的 SSH 代码地址，命中即按 `crash_status=11` 上报。
它证明了“guest SSH 执行 → L0 → shm 0x2001 → fuzzer waitpid”的完整链路；
真实崩溃地址仍需按 `4-崩溃判定v1-实现与验证.md` §4 的 log 模式标定。

## 复现

```bash
# tap1 已配置 172.16.100.1/24；QEMU 以当前用户附着即可
export C2900_KERNEL=$PWD/vendor/c2900/firmware/C2900-UN.150.BIN
export C2900_FUZZ_CONFIG=$PWD/logs/ssh-verify-20261029/fuzz.json
export C2900_FUZZ_RUNDIR=/tmp/ssh-verify-run
export C2900_NIC_ARGS="tap,model=octeon-eth,ifname=tap1,script=no,downscript=no,mac=52:54:00:12:34:56"
./runtime/start_c2900_fuzz.sh start
./runtime/start_c2900_fuzz.sh wait 1800
python3 runtime/fuzz_client.py save
python3 runtime/fuzz_client.py round && python3 runtime/protocols/ssh/tools/ssh_real.py
python3 runtime/fuzz_client.py end
```

运行期注意：SSH 密钥生成期间连接会被关闭且无 banner；应先用
`show ip ssh` / 真实登录确认服务就绪，再 `0x01` 保存快照。
