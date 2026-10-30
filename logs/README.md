# 验证日志索引

日志主要来自 2026-10-19 与 2026-10-29 的实测运行。每个目录包含：

- `coverage.log`：QEMU 侧覆盖率模块日志（配置、baseline、每轮 TB/边、IOS 统计、别名合并）；
- `forkserver.log`：0x01/0x02/0x03 命令、快照保存/恢复；
- `serial.log`：guest 串口（启动链、banner）。

## `verification-2026-10-19/crash/`（崩溃反馈验证）

`scripts/verify-crash.sh 4` 的输出：

- `coverage-on.log`：`crash_mode=on` + `panic_addrs=["0x81000000"]`
  - `crash candidate: reason=panic_addr pc=0xffffffff81000000 status=0x0000000b`
  - shm `0x2001` = `0x0000000b`（SIGSEGV）
- `coverage-log.log`：`crash_mode=log` 同样记录 candidate，但 shm 保持
  `0xdeadbeef`。

另用同一 sentinel 配置运行真实 `afl-fuzz -Z`，dry run 报
`Test case 'id:000000,orig:req' results in a crash`，证明 waitpid 状态被
AFLNet 正确判为 crash。

## `verification-2026-10-19/pristine-reextract/`（重新解压 tarball 对比）

把 `<PRIVATE_ASSETS>/qemu-8.2-eval(1).tar.gz` 重新解压覆盖到
`<PRIVATE_ASSETS>/qemu-8.2-eval` 后：

- `VERSION`/`hw/mips/soc_eval.c`/`roms/machcore/libmachcore.so` 与解压前
  逐字节相同；
- tarball 中没有 `hw/mips/octeon.c`/`octeon-eth` 等厂商网络模型；
- 用纯净源码重新 configure/build 后启动 M7：
  `show ip interface brief` 只有表头，`show diag` 仍是 `Slot 0 Unknown (type 0)`；
- 结论：tarball 是同一份去品牌化 `soc-eval` 基线，不是厂商源码；网络阻塞不变；
- 对比后已重新应用 `qemu/patches`、重建 fuzz binary，并跑通
  `verify-crash.sh`。

## `verification-2026-10-19/ssh-snapshot/`（SSH 降级快照验证）

`C2900_SSH_SKIP_INTERFACE=1` 在 eval 板级上验证 SSH 配置/快照/重置链路：

- `snap_ssh.json`：`config_exit=0`、`snapshot_status=1`、kernel 为 M7、
  快照时 `edges=20375`；
- `configure.log`：`SSH configuration complete (verified VTY transport ssh; interface setup skipped...)`；
- `coverage.log`：`0x02` 后 edges 20375 → 133，`0x03` 正常；
- `forkserver.log`：`saving snapshot 'snap_ssh'` / `snapshot restored`。

**注意**：该快照没有网络接口，不能用于真实 SSH 协议 fuzz。

## `verification-2026-10-19/ssh-blocked/`（eval 板级网络阻塞）

- `console-history.txt` / `console.log`：`show ip interface brief` 为空、
  `interface Gi0/0` 被拒绝、`%PA-2-UNDEFIO`、`Slot 0 type 0`；
- `SUMMARY.md`：阻塞结论与后续路线。

## `verification-2026-10-19/http-prepare/`（协议适配端到端验证）

`scripts/protocol.sh prepare http` 的实测产物：

- `snap_http.json`：快照名 `snap_http`、配置脚本 `runtime/protocols/http/configure.py`、
  固件/config hash、快照时覆盖率 `edges=62430`；
- `configure.log`：`Web configuration complete (HTTP + HTTPS, clock set, both servers Enabled)`；
- `snapshot.log`：`0x01 save snapshot -> status=1`；
- `console-final.txt`：IOS 配置后的 tmux 回滚缓冲；
- `coverage.log` / `forkserver.log`：快照名为 `snap_http`，`0x02` 恢复后 edges 从
  62,430 降到 133（per-round reset），`0x03` 正常结束。

## `verification-2026-10-19/`（覆盖率真实性/兼容性验证）

- `coverage-verify.log`：`scripts/verify-coverage.sh m7` 的完整输出
  - paused edges=0；cont 后 1544；stop 后 hash 两次完全相同；cont 后 hash 变化；
  - mid-boot 快照 1544 edges；0x02 reset 后 20 edges；
- `aflnet-afl.log`：真实 `afl-fuzz -Z` 连接本 QEMU 的运行日志（dummy HTTP 对端）；
- `aflnet-fuzzer_stats`：AFLNet 侧统计
  - `execs_done=11`、`bitmap_cvg=2.36%`、`stability=100.00%`、`paths_total=1`；
  - 2.36% × 65536 ≈ 1547，与 QEMU shm 的 1545 条边一致。

> 该 AFLNet 测试只验证 FIFO/shm/bitmap 链路；网络对端是宿主侧 dummy HTTP
> 服务器，不是 C2900 的 HTTP 服务。

## `m9-validated/`（最终运行，`exclude_baseline=true`）

- 固件：M9 外层镜像（副本已修 `e_machine`）
- 配置：`runtime/c2900-ios.json`（`.text` 物理范围 `0x01000000–0x05956000`）
- 结果：
  - 启动到 banner：约 11.5 min
  - banner 快照：139,539 TB / 62,144 edges，`0x01` ~0.74 s
  - 每轮：75 TB / 85–86 edges，`0x02` ~1.4 s、`0x03` ~1.0 s
  - `phys_missing=0`；boot 期间 `alias_merge≈58,498,990`
  - 连续 3 轮稳定；`loadvm` 后 VM 继续运行

## `m7-validated/`（M7，`exclude_baseline=false` 对照）

- 固件：`C2900-UN.150.BIN`（M7 解压镜像）
- 配置：`runtime/c2900-ios.json`（当时使用 `.text` 上界 `0x05956000`）
- 结果：
  - 启动到 banner：约 8 min
  - banner 快照：138,505 TB / 61,987 edges
  - 每轮恢复 baseline：TB 数恒为 138,505，边数 61,987–61,988
  - 用于验证"恢复 baseline、每轮完全可复现"的参考实现行为

## `m7-baseline/`（首次 M7 运行）

- 早期统计版本（尚未加入 `ios tb stats` 与 `exclude_baseline`）
- 用于确认：savevm/loadvm、forkserver 0x01/0x02/0x03、canonical 覆盖率基本链路
- 记录了 61,987 edges / 138,505 TB 的初始基线，以及 8 条 alias merge 样例
