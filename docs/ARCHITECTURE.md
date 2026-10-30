# C2900SysFuzzer 技术说明

> 本文由项目早期 README 迁移而来，保留原理、协议与实测细节；
> 快速开始与私有资产说明见仓库根目录 [`README.md`](../README.md) 和
> [`PRIVATE-ASSETS.md`](PRIVATE-ASSETS.md)。

Cisco C2900 IOS（MIPS64 Octeon）在 QEMU 系统级仿真下的 **覆盖率引导 fuzz 原型**。

本项目把 `QemuSysFuzzer`（Malta/Linux 系统级 fuzz 框架）
的 TB 插桩、AFL edge bitmap、快照型 forkserver 迁移到
**C2900 IOS 固件**，并在真实的 C2900 固件上完成了：

- 带插桩启动 IOS 到 `Press RETURN to get started!`；
- `savevm/loadvm` 整机快照与每轮覆盖率重置；
- 物理地址归一化的 canonical 覆盖率（KSEG0 / 运行时 `0x21` / 符号扩展别名合并）；
- 与 AFLNet `-Z` 完全相同的 FIFO + shm 协议。

> 当前是**原型**：按协议配置、配置完成自动快照、canonical 覆盖率、forkserver
> 协议均已就绪；崩溃判定 v1（L0/L4/L1 → shm `0x2001`）已实现并用合成哨兵
> 验证；真实 IOS 崩溃标定仍待完成。
>
> **2026-10-27 更新**：项目已切到 `qemu-8.2-eval-v2`（`-M octeon` +
> `octeon-eth`，IOS 可枚举 Gi0/0）；10-19 的 `soc-eval` 板级阻塞不再适用。
> 旧 v1 基线已从 `vendor/qemu` 移除，补丁只对 v2/v3 应用。
>
> **2026-10-29 更新**：已切到 `qemu-8.2-eval-v3`。QEMU 源码与 v2 相同，
> 闭源 `libmachcore.so` 修复了 guest→host TX；M7 + `-fuzz` 已跑通
> banner → `0x01` → 多轮 `0x02/0x03`，带 ARP 输入覆盖稳定、无崩溃。
> 宿主↔guest 数据面已可用；真实 AFLNet 协议发包仍待接入。
> 验收记录：`logs/v3-fuzz-acceptance-20261029/`。
>
> **2026-10-29 真实 SSH 验收**：`tap1` + Paramiko 完成真实登录
> （`SSH-1.99-Cisco-1.25`，admin/cisco）；无输入基线 132 边，真实 SSH 轮
> 13,115 / 13,784 边（位图新增 12,984）；L0 SSH 哨兵使 shm `0x2001=0x0b`，
> `0x02` 后恢复 `0xdeadbeef`。证据：`logs/ssh-verify-20261029/`。
>
> **正式 campaign 编排**：`runtime/protocols/ssh/campaign.sh` 提供
> `start`（tap 启动→banner→SSH 真实登录就绪→0x01 快照）、`aflnet`、
> `status`、`stop`；生产配置 `config/fuzz.aflnet-{m7,m9}.json` 默认
> `crash_mode=off`，种子目录 `seeds-aflnet/`。
>
> **2026-10-29 体积裁剪**：移除 `vendor/qemu/src/build`（可由
> `scripts/build-qemu.sh` 重建）、`vendor/c2900/bin`（厂商参考二进制）、
> 未使用的 M7 外层镜像与 `155-3.M` 镜像；保留 M7 内层/M9 外层、disk0、
> flash/rommon、QEMU 源码与预编译二进制；预编译二进制已 `strip --strip-debug`
> （59 MB → 17 MB）。当前约 655 MB。
>
> 2026-10-27 用重建的 `vendor/qemu` 产物实测：M7 直启到 banner 404 s，
> `Data Bus Error` 0 次。

---

## 目录

- [1. 结论速览](#1-结论速览)
- [2. 背景与原理](#2-背景与原理)
- [3. 目录结构](#3-目录结构)
- [4. 环境要求](#4-环境要求)
- [5. 快速开始](#5-快速开始)
- [6. 配置参考](#6-配置参考)
- [7. forkserver 协议与 AFLNet 对接](#7-forkserver-协议与-aflnet-对接)
- [8. 覆盖率验证与实测结果](#8-覆盖率验证与实测结果)
- [9. 日志与排障](#9-日志与排障)
- [10. 已知限制与下一步](#10-已知限制与下一步)
- [11. 代码索引](#11-代码索引)
- [12. 参考资料](#12-参考资料)
- [13. FAQ](#13-faq)

---

## 1. 结论速览

| 问题 | 结论 |
|---|---|
| 插桩在哪一层？ | QEMU/TCG 层，`accel/tcg/cpu-exec.c` 的 TB 执行出口；与 guest 是 IOS 还是 Linux 无关 |
| 需要改 guest 吗？ | 不需要。IOS 固件保持原样 |
| 原框架能直接用于 IOS 吗？ | 插桩/forkserver/shm 可复用；**目标识别、ASID/用户态、地址模型必须重写** |
| 覆盖率地址怎么选？ | 物理地址（`tb_page_addr0()`）；KSEG0/运行时/符号扩展别名归一到同一 key |
| 快照能工作吗？ | 能。`-snapshot` 下 raw 盘的临时 qcow2 overlay 支持 `savevm/loadvm` |
| 启动要多久？ | v2 实测：未插桩 M7 ~7 min；带 `-fuzz` ~22 min（宿主负载会影响） |
| 每轮多快？ | `0x02` ~1.4 s、`0x03` ~1.0 s（含 1 s 等待） |
| M9 能启动吗？ | 能。v2 的 `elf_ops.h` 接受 `e_machine=0xC2`，可直接 `-kernel`；脚本的 0x0008 副本逻辑仅用于兼容旧 v1 |
| 现在能接 AFLNet 吗？ | 协议/FIFO/shm 链路已通；v2 已枚举 Gi0/0，但宿主↔guest 数据面（tap/user）尚未验证，SSH/HTTP 真实发包仍待跑通 |
| 崩溃判定呢？ | v1 已实现：L0 TB 哨兵 / L4 异常钩子 / L1 guest reset → shm `0x2001`；已用合成哨兵验证 AFLNet 判 crash，真实 IOS 崩溃地址/掩码需标定 |

### 实测数字（2026-10-19）

| 项 | M9 | M7（`.150`） |
|---|---|---|
| 插桩启动到 banner | ~11.5 min | ~8 min |
| banner 快照 TB 数 | 139,539 | 138,505 |
| banner 快照 edge 数 | 62,144 | 61,987 |
| `0x01` 快照保存 | ~0.74 s | ~0.45 s |
| 每轮 TB / edge | 75 / 85–86（`exclude_baseline=true`） | 138,505 / 61,987–61,988（`exclude_baseline=false` 对照） |
| `loadvm` 后 VM | 继续运行（monitor PC 变化） | 继续运行 |
| `phys_missing` | 0 | 0 |

> M7 的每轮 138,505 / 61,987–61,988 是 `exclude_baseline=false`（恢复 baseline）
> 对照运行的结果；M9 的每轮 75 / 85–86 是 `exclude_baseline=true` 时从空 bitmap
> 开始的真实 per-round 覆盖。M7 在 `exclude_baseline=true` 下的 per-round 数字
> 尚未单独记录（预期与 M9 同量级）。banner 快照的 baseline 分别为 M9
> 139,539 TB / 62,144 edges、M7 138,505 TB / 61,987 edges。

---

## 2. 背景与原理

### 2.1 系统级 fuzz 的四个问题

QemuSysFuzzer 把“整机仿真 + 网络 fuzz”拆成四件事：

1. **输入注入**：真实网络包经 guest 的 TCP/IP 栈进入服务；
2. **覆盖率**：从整机执行流中采集目标代码的覆盖率；
3. **崩溃判定**：判断本轮是否触发了目标异常；
4. **状态重置**：每轮恢复到同一初始状态（比 fork 更彻底）。

本项目在本阶段完成 **1 的启动链路（不含发包）、2 和 4**；3 留待后续。

### 2.2 QemuSysFuzzer 的插桩链路

```text
guest 指令
  → TCG 翻译成 TB（Translation Block）
  → [fuzz 模式] 禁用 goto_tb 直接链跳（CF_NO_GOTO_TB | CF_NO_GOTO_PTR）
  → 每个 TB 执行后回到 cpu_tb_exec() 出口
  → fuzz_coverage_record_tb(pc, phys, icount, asid, user_mode)
  → edge_bitmap[cur_loc ^ prev_loc]++
  → shm key 0x2000（AFL 兼容 64K bitmap）
  → fuzz/forkserver.c：0x01 保存快照、0x02 loadvm+重置、0x03 本轮结束
```

关键点：

- **TB 粒度**：与基本块对应，出口 hook 开销可控；
- **禁用链化**：保证每个 TB 都经过统一记录点（代价是仿真变慢）；
- **共享内存旁路**：fuzzer 直接读 bitmap，不发命令；
- **快照重置**：整机 RAM/CPU/设备状态回滚，不是 fork。

### 2.3 C2900 固件模型

以 M9 外层镜像为例（详见
`（内部笔记，未公开）`）：

```text
c2900-universalk9-mz.SPA.150-1.M9.bin
├── ELF32 BE, e_machine=0xC2, entry 0x81000000
├── PT_LOAD @0x194, vaddr 0x81000000
├── stub/（解压/引导代码）
├── FEEDFACE header
├── ZIP（deflate）
│   └── C2900-UN.BIN（内层 IOS ELF，e_machine=0x0008）
│       ├── .text  @0x81000000, 0x4956000 B  → 物理 0x01000000–0x05956000
│       ├── .rodata @0x85956000
│       ├── .data  @0x87827090
│       ├── sreloc @0x76183A0
│       └── 100 个内嵌资源 ZIP
└── NOTE（签名/证书）
```

启动链：

```text
复位 PC 0xBFC00000
  → mips64_mixed.bin（-bios，自写 ROM 微码）
  → 从 cisco_iomem（PA 0x16000000 / KSEG1 0xB6000000）读入口 0x81000000
  → 跳转 0x81000000（KSEG0 / PA 0x01000000）
  → stub 解压 / 加载内层 IOS
  → IOS 初始化 MMU/TLB
  → 运行时 0x21xxxxxx（text）/ 0x25xxxxxx（data）
```

**M7 与 M9 的差异**：M7 的 `C2900-UN.150.BIN` 是已解压内层镜像（`e_machine=8`，
`.text` `0x4950000`）；M9 是外层（`e_machine=0xC2`，`.text` `0x4956000`，stub 自行解压）。
配置里的 `.text` 上界不同，见 §6。

### 2.4 原实现 → C2900 的映射

| 原 QemuSysFuzzer（Linux 用户态） | C2900 IOS | 本项目的处理 |
|---|---|---|
| ASID 区分进程 | 单地址空间，无进程 | 去掉 ASID 过滤 |
| 用户态标志 `MIPS_HFLAG_UM` | 全程内核态 | 去掉用户态过滤 |
| ELF `_start` + 入口 8 条指令指纹 | 内层 ELF stripped，无 `_start`；入口 ≠ 服务入口 | IOS 模式不做指纹识别 |
| ASLR 偏移 | 无 ASLR；有 TLB 映射 | 不做 ASLR |
| `virt_addr` 做 edge hash | KSEG0/运行时/符号扩展多别名 | **物理地址 canonical** |
| `.text` 物理范围过滤 | 范围仍是稳定身份 | 保留，按内层镜像 `.text` |
| Linux syscall exit / 信号 | 无 Linux syscall；IOS 可能自愈 | **本阶段不实现** |
| `savevm/loadvm` | 整机快照 | 保留；raw 盘用 `-snapshot` overlay |

### 2.5 为什么必须用物理地址

同一段 IOS 代码会以至少四种地址出现：

| 形式 | 示例 | 说明 |
|---|---|---|
| KSEG0 | `0x8111c704` | 链接地址/直接映射 |
| 符号扩展 | `0xffffffff8111c704` | QEMU 中的 64 位符号扩展 |
| 运行时 TLB | `0x2111c704` | IOS 初始化 MMU 后的低地址映射 |
| 物理 | `0x0111c704` | `tb_page_addr0()` 给出 |

如果按虚拟地址 hash，同一基本块会产生 2–4 套边，coverage 完全失真。本项目在
`fuzz/coverage.c:fuzz_canonical_addr()` 中统一使用物理地址；实测日志：

```text
alias merge #1: canon=0x000000000111c704 virt 0xffffffff8111c704 -> 0x000000002111c704
...
alias merge #8: canon=0x000000000111c798 virt 0xffffffff8111c798 -> 0x000000002111c798
```

同一物理块先以符号扩展 KSEG0 执行、再以运行时 `0x21` 执行，`GHashTable` 中
始终只有一条 canonical 记录。

### 2.6 为什么 IOS 默认 `exclude_baseline=true`

C2900 的 boot/CLI 会执行几乎整个 `.text`：

- banner 时已命中 **62,144 / 65,536** 条边（约 95% map density）；
- 如果每轮把 boot baseline 写回 bitmap，fuzzer 开始前 64K map 就几乎满了，
  新路径很难被识别；
- 因此 IOS 默认每轮 `0x02` **清空** bitmap/TB 表，只记录快照点之后的执行。

实测效果：banner 快照后每轮只有 **75 TB / 85–86 edges**，map density 约 0.13%，
给 fuzz 留出了完整空间。`exclude_baseline=false` 仅用于对照验证“每轮完全可复现”。

---

## 3. 目录结构

```text
<仓库根目录>/
├── README.md                 # 快速开始 / 私有资产入口
├── THIRD_PARTY.md
├── .gitignore / .gitmodules
├── docs/                     # 架构、私有资产、验收记录
├── vendor/
│   ├── qemu/
│   │   ├── SOURCE.md         # 私有基线来源与哈希
│   │   ├── src/              # [私有] qemu-8.2-eval-v3 + 补丁（不入库）
│   │   ├── bin/              # [私有] 预编译插桩 QEMU
│   │   └── lib/              # [私有] libmachcore.so（v3）
│   ├── c2900/
│   │   ├── firmware/         # [私有] 固件/磁盘；仓库只留 checksums/README
│   │   ├── test/             # 配置/验证脚本
│   │   └── lib/              # [私有] 运行库
│   └── aflnet/               # AFLNet submodule（内容不入库）
├── fw_probe/                 # 固件静态分析工具
├── logs/                     # 验收日志摘要
├── qemu/
│   ├── README.md             # QEMU 补丁说明
│   ├── apply-patch.sh        # 把补丁应用到 QEMU 基线
│   └── patches/              # 0001-c2900-fuzz-overlay.patch
├── runtime/
│   ├── start_c2900_fuzz.sh   # 启动/停止/状态/等待 banner/console
│   ├── fuzz_client.py        # 最小 forkserver 客户端
│   ├── coverage_probe.py     # 独立读取/导出/比较 shm 0x2000 bitmap
│   ├── c2900-ios.json        # M9 配置
│   ├── c2900-ios-m7.json     # M7 配置
│   ├── test-configs/         # 崩溃判定/覆盖率反馈测试配置
│   ├── c2900-program/        # 非网络程序插桩验证
│   └── protocols/            # 按协议隔离的运行区域
├── scripts/                  # bootstrap / 资产 / 构建 / 检查 / 验证
└── tools/coverage2.c         # 可选 TCG 覆盖率插件源码
```

> **分发方式**：公开仓库只包含自有代码/脚本/补丁（约 2.6 MB 跟踪内容）；
> `vendor/` 下的固件、QEMU 源码/二进制、`libmachcore.so` 与运行库均为私有资产，
> 由维护者私下提供；AFLNet 通过 submodule 获取。详见
> [`PRIVATE-ASSETS.md`](PRIVATE-ASSETS.md)。
>
> 如需制作完整的离线包（含私有资产），把资产按目录放好后自行打包；
> 注意不要把该包上传到公开仓库、Release 或 LFS。

---

## 4. 环境要求

### 4.1 宿主

- Linux x86_64；
- `bash`、`tmux`、`socat`、`python3`、`ninja`、`cc`、`pkg-config`；
- glib2 开发库（QEMU 构建需要）；
- **sudo 仅在使用 tap 网络时需要**；原型默认用 `-nic socket`，不需要 sudo。

### 4.2 QEMU 源码基线

项目内 `vendor/qemu/src` 是已叠加本项目 `fuzz/` 改造的
`qemu-8.2-eval-v3` 基线：

- QEMU 8.2.0（2026-10-28 的 v3 源码包；QEMU 源码与 v2 逐字节相同）；
- 含 Octeon 板级 `hw/mips/soc_eval.c`（机器名 `-M octeon`）与
  `octeon-eth` 网卡；
- 含 `roms/machcore/libmachcore.so`（v3，125,360 B，BUILD_ID
  `EVAL-20261028-F`；相对 v2 修复 guest→host TX）与 `mach_*` 设备；
- 已包含 `fuzz/`、`include/fuzz/` 与 core 修改；
- 预编译二进制：`vendor/qemu/bin/qemu-system-mips64`；
- 运行库：`vendor/qemu/lib/libmachcore.so`。

> 旧 v1 eval 树（`soc-eval`/`eth`）已移除；上游普通 QEMU 8.2、参考实现
> QemuSysFuzzer/Qemu（8.2.10）**不能**直接替换：前者没有 C2900 板级/设备库，
> 后者的版本/机型不匹配。

### 4.3 C2900 仿真包

项目内 `vendor/c2900`，需要：

```text
firmware/C2900-UN.150.BIN                          # M7 解压内层镜像
firmware/c2900-universalk9-mz.SPA.150-1.M9.bin     # M9 外层镜像
firmware/mips64_mixed.bin                          # -bios ROM 微码
firmware/rommon_var.bin                            # -drive id=iomem
firmware/flash.bin                                 # pflash
firmware/disk0.raw                                 # FAT16 disk0
```

运行前建议执行环境检查：

```bash
cd <仓库根目录>
./scripts/check-env.sh
```

---

## 5. 快速开始

### 5.1 构建

```bash
cd <仓库根目录>
./build.sh                 # 一键：资产 + AFLNet + QEMU + 启动检查
# 只重建 QEMU：
./scripts/build-qemu.sh
```

等价于：

```bash
ninja -C <仓库根目录>/vendor/qemu/src/build qemu-system-mips64
```

构建脚本会把产物同步到 `vendor/qemu/bin/`。若 `vendor/qemu/src` 不存在，
`build.sh` 会从目录形式私有包的 `qemu/src` 安装源码并应用补丁；只有使用
旧 tarball 回退时，`prepare-qemu-src.sh` 才会解包
`vendor/qemu/qemu-8.2-eval-v3.tar.gz`。也可以手动执行：

```bash
./scripts/prepare-private-assets.sh <私有资产目录>   # 安装 qemu/src 等
./scripts/prepare-qemu-src.sh                  # 确保 fuzz 补丁已应用
./scripts/build-qemu.sh                        # 默认 vendor/qemu/src
./scripts/build-qemu.sh /path/to/qemu-src      # 或指定已有源码树
```

### 5.2 启动（默认 M9）

```bash
./scripts/start.sh start
./scripts/start.sh wait 900        # 插桩 M9 约 11.5 min 到 banner
```

实际执行的是 `runtime/start_c2900_fuzz.sh`。它会：

- 在 tmux 会话 `c2900fuzz` 中启动 QEMU；
- 为 M9 外层镜像生成 `e_machine` 修补副本（不改原固件）；
- 使用 `-snapshot` 保护 raw 盘；
- 创建 forkserver FIFO 和 shm；
- 创建串口 socket `127.0.0.1:5554` 和 tmux `console` 会话，供 C2900 配置脚本使用；
- 串口输出写 `runtime/run/serial.log`；
- monitor 在 `runtime/run/monitor.sock`。

查看状态：

```bash
./scripts/start.sh status
tail -f runtime/run/serial.log
tail -f runtime/run/Logs/coverage.log
```

### 5.3 启动（M7，迭代更快）

```bash
C2900_KERNEL=<仓库根目录>/vendor/c2900/firmware/C2900-UN.150.BIN \
C2900_FUZZ_CONFIG=<仓库根目录>/runtime/c2900-ios-m7.json \
./scripts/start.sh start
./scripts/start.sh wait 900        # M7 约 8 min
```

### 5.4 按协议准备（协议适配）

每个协议有独立运行区域 `runtime/protocols/<proto>/`：

```text
protocol.env        # 配置脚本、快照名、端口、AFLNet -P、超时、备注
config/fuzz.json    # QEMU -fuzz 配置（含 snapshot_name）
logs/               # console/configure/snapshot/coverage 日志
snapshots/          # 快照元数据 JSON（时间/脚本/hash/覆盖率）
seeds/              # AFLNet 种子
out/                # AFLNet 输出
```

命令：

```bash
./scripts/protocol.sh list
./scripts/protocol.sh prepare http      # 启动→banner→configure.py→0x01 快照
./scripts/protocol.sh configure http    # 对已运行实例重跑配置→0x01（调试/续跑）
./scripts/protocol.sh status http
./scripts/protocol.sh aflnet-cmd http
./scripts/protocol.sh stop http
```

每个协议目录内的 `configure.py` 是从 `vendor/c2900/test/config_*.py` 复制的
适配版：命令逻辑不变，只把 prompt/RSA/文本等待超时改成可配置值
（插桩后的 QEMU 很慢，原 30/60/120s 容易误超时）。超时环境变量：

```text
C2900_CONFIG_PROMPT_TIMEOUT=300
C2900_CONFIG_TEXT_TIMEOUT=600
C2900_CONFIG_RSA_TIMEOUT=900
```

`prepare` 的流程固定为：

1. 启动 QEMU（带 `-fuzz`）和 `console` 会话；
2. 等待 IOS banner；
3. 运行 `protocol.env` 指定的 C2900 配置脚本（`vendor/c2900/test/config_*.py`）；
4. **配置脚本退出码为 0 后**，才向 forkserver 发送 `0x01` 保存快照；
5. 把快照名、配置脚本、固件/配置 hash、覆盖率计数写入
   `snapshots/<snapshot_name>.json`；
6. QEMU 保持运行，等待 AFLNet（或手动调试）。

这满足“每个协议的快照保存于运行完对应配置脚本时”的要求：配置失败时不会
保存快照；快照名按协议区分（`snap_http`/`snap_https`/`snap_ssh`/...）。

实测 `prepare http`（2026-10-19）：

```text
Web configuration complete (HTTP + HTTPS, clock set, both servers Enabled)
0x01 save snapshot -> status=1, bitmap edges=62430
forkserver: saving snapshot 'snap_http' / snapshot saved: snap_http
0x02 loadvm -> status=1, edges 62430 -> 133
0x03 end -> status=1, edges=133
快照元数据：runtime/protocols/http/snapshots/snap_http.json
```

日志与元数据样例见 `logs/verification-2026-10-19/http-prepare/`。

SSH 实测（2026-10-19）：

- eval `soc-eval` 板级没有 IOS 可见网络接口（`show ip interface brief` 为空、
  `interface Gi0/0` 无效、`Slot 0 type 0`、`%PA-2-UNDEFIO`），真实 SSH 网络 fuzz
  被阻塞；
- 用 `C2900_SSH_SKIP_INTERFACE=1` 验证了 SSH 降级链路：
  `configure` 成功 → `snap_ssh` status=1（edges=20375）→ `0x02` 后 133 edges；
- 证据：`logs/verification-2026-10-19/ssh-snapshot/`、
  `logs/verification-2026-10-19/ssh-blocked/`；
- 详见 `（内部笔记，未公开）`。

> **2026-10-27 更新**：v2/`octeon` + `octeon-eth` 已枚举 Gi0/0、Gi0/1，
> 上述 `soc-eval` 板级阻塞不再成立；下一步是 tap 连通性与真实 SSH/HTTP
> 发包验证（见 `runtime/protocols/ssh/README.md`）。

当前协议入口：

| 协议 | 配置脚本 | 快照名 | 端点 | AFLNet `-P` |
|---|---|---|---|---|
| HTTP | `configure.py`（源自 `config_web.py`） | `snap_http` | `tcp://<guest>:80` | `HTTP` |
| HTTPS | `configure.py`（源自 `config_web.py`） | `snap_https` | `tcp://<guest>:443` | `TLS`（需验证 SSLv3） |
| SSH | `configure.py`（源自 `config_ssh.py`） | `snap_ssh` | `tcp://<guest>:22` | `SSH` |
| SNMP | `configure.py`（源自 `config_snmp.py`） | `snap_snmp` | `udp://<guest>:161` | `SNMP` |
| IPsec | `configure.py`（源自 `config_ipsec.py`） | `snap_ipsec` | `udp://<guest>:500` | 无（自定义 harness） |
| Telnet | `configure.py`（源自 `config_telnet.py`） | `snap_telnet` | `tcp://<guest>:23` | 无（自定义提取器） |

注意：

- 网络后端由 `protocol.env` 的 `NET_MODE` 控制：
  - `socket`（默认）：QEMU `-nic socket`，只适合启动/覆盖率，AFLNet 到不了 guest；
  - `tap`：`-nic tap,ifname=tap1`，需先 `sudo scripts/setup-tap.sh tap1`
    （本机 tap0 被其他项目占用；不要删除/复用 tap0）；
  - `user`：`-nic user,hostfwd=...`，需要 QEMU 编入 libslirp（当前预编译没有）。
- C2900 配置脚本固定使用 tmux 会话名 `console`；同一时间只运行一个 prepare；
- `prepare` 保存快照后请立即启动 AFLNet：AFLNet 启动时会再发一次 `0x01`，
  若中间手动改动了 guest 状态，新快照会以改动后的状态为准；
- 配置脚本的输出在 `logs/configure.log`，控制台原始输出在 `logs/console.log`；
- IPsec 的 `securityk9` license 需要先执行 `config_ipsec_license.py` 并重启，
  本流程默认 license 已生效；否则 `config_ipsec.py` 会失败且不会保存快照；
- 快照保存在 QEMU 进程的临时 overlay 中；停止 QEMU 后失效。要持久化请使用
  配置里的 `snapshot_device`（专用 qcow2）。

### 5.5 快照与轮次

banner 后：

```bash
./runtime/fuzz_client.py wait            # 等 IPC 就绪
./runtime/fuzz_client.py save            # 0x01：保存 banner 快照
./runtime/fuzz_client.py round           # 0x02：loadvm + 清空覆盖率 + 继续
./runtime/fuzz_client.py end             # 0x03：等待并输出本轮覆盖率
./runtime/fuzz_client.py verify --rounds 3
./runtime/fuzz_client.py stats           # 当前 shm 边数/exit status
```

`verify` 会再保存一次快照并跑多轮，输出类似：

```text
round 1: 0x02 status=1, edges=85, 0x03 status=1, edges=85, exit=0xdeadbeef
round 2: 0x02 status=1, edges=86, 0x03 status=1, edges=86, exit=0xdeadbeef
verify: rounds=[85, 86, 86], baseline=85, stable=yes
```

### 5.6 一键冒烟

```bash
./scripts/smoke-test.sh m7 900     # 启动 M7 → banner → 快照 → 2 轮 → 停止
./scripts/smoke-test.sh m9 1200    # 启动 M9
```

### 5.7 覆盖率验证（约 20 秒，不需要等 banner）

```bash
./scripts/verify-coverage.sh m7
./scripts/verify-aflnet.sh 25
./scripts/verify-crash.sh 4
```

- `verify-coverage.sh`：暂停启动 bitmap=0 → `cont` 后增长 → `stop` 后冻结 →
  `cont` 后再次变化 → 快照/`loadvm` 后重置；
- `verify-aflnet.sh`：用真实 `afl-fuzz -Z` 连接本 QEMU，dummy HTTP 服务作为
  宿主侧对端，验证 AFLNet 能读到非零、稳定的 bitmap（`bitmap_cvg` 与 shm
  边数对得上）。
- `verify-crash.sh`：用合成 TB 哨兵验证 `crash_mode=on` 写 `0x0b` 到 shm
  `0x2001`、`crash_mode=log` 只记录不改状态。

### 5.7.1 非网络程序插桩验证（C2900 M9 启动 stub）

在**能启动的 C2900 仿真内部**选一个特定非网络程序：M9 外层镜像的启动
解压 stub（VA `0x81000000`，外层文件偏移 `0x194`，`0x18000` 字节）。它
在 IOS 之前运行，保存寄存器/探测内存/调用解包主流程（`0x81002a50`），
不依赖网络。验证链路：

```text
M9 外层镜像 → ROM 在 0x81000000 启动 stub
  → fuzz/coverage.c 命中 snapshot_trigger=0x81000000
  → 自动 savevm（snap_stub）
  → 每轮 0x02 loadvm 回到 stub 入口，0x03 收集
  → 覆盖率严格限定 stub 物理区 0x01000000-0x01018000
```

`snapshot_trigger` 已在 `fuzz/coverage.c` 实现（IOS 模式命中地址经
`/tmp/qemu_fuzz_auto` 通知 forkserver 自动快照，每进程一次）。需要重建
QEMU。全程 `-nic none`，不需要 tap/sudo：

```bash
./scripts/build-qemu.sh
./scripts/verify-c2900-program.sh --rounds 5
```

实测：触发后 5 轮边数 26/27/27/27/27，TB 集 23 个且每轮完全相同，
所有地址都在 stub 区；热点 TB（校验/累加循环）执行 700 万次以上。
详见 `runtime/c2900-program/README.md`（含 monlib 负结果与主 `.text`
CLI 命令差分等补充分析）。该空间与 C2900 runtime 共用
`/tmp/qemu_fuzz_*` 和 shm key，不能并行运行。

### 5.8 停止

```bash
./scripts/start.sh stop
```

停止脚本会清理 `/tmp/qemu_fuzz_ctl`、`/tmp/qemu_fuzz_st`、shm `0x2000/0x2001`、
monitor socket 和临时内核副本。

---

## 6. 配置参考

配置文件为 JSON（逐行手写解析，支持单行数组）。示例：
`runtime/c2900-ios.json`（M9）、`runtime/c2900-ios-m7.json`（M7）。

### 6.1 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `mode` | int | `0/1/2` 为 Linux 模式；`3` 为 IOS 手动配置模式 |
| `target_type` | string | `"ios"` 或省略（Linux）。`mode=3` 时自动设为 IOS |
| `addr_mode` | string | `"phys"`（推荐）、`"kseg0"`、`"virt"`。IOS 未配置时强制 phys |
| `coverage_types` | array | `"block"`、`"edge"`、`"trace"`；可单行或分行 |
| `exclude_baseline` | bool | IOS 默认 `true`；每轮清空 bitmap，不写回 boot baseline |
| `inst_ratio` | int | edge 采样比例，`65536`=100%；0 也按 100% 处理 |
| `coverage_start` | hex string | canonical 过滤下界（含） |
| `coverage_end` | hex string | canonical 过滤上界（不含） |
| `text_start` | hex string | 内层镜像 `.text` 链接地址（IOS 下仅用于推导范围/日志） |
| `text_size` | hex string | `.text` 大小 |
| `runtime_delta` | hex string | 运行时 `0x21` → KSEG0 的差值；C2900 实测 `0x60000000` |
| `firmware_entry` | hex string | 固件入口，记录用（`0x81000000`） |
| `service_entry` | hex string | 服务入口（预留，0=未知）；SSH 已静态标定为 `0x8383e3d8`，见 `runtime/protocols/ssh/config/firmware-analysis.json` |
| `snapshot_trigger` | hex string | 命中该地址自动快照（IOS 模式已实现，0=手动；每进程一次） |
| `auto_snapshot` | bool | 自动快照；原型为 false |
| `snapshot_device` | string | 可选：只对该块设备做内部快照（不依赖 `-snapshot` 时用） |
| `crash_mode` | string | `"off"`（默认）/`"log"`/`"on"`；`on` 把崩溃候选写入 shm `0x2001` |
| `panic_addr` / `panic_addrs` | hex / array | L0 哨兵地址；virt/canonical/低 32 位任一匹配 |
| `crash_status` | int | 崩溃时写入 waitpid 低 7 位；默认 `11`（SIGSEGV） |
| `crash_on_reset` | bool | L1：guest reset 是否判崩溃；默认 false |
| `crash_exceptions` | array | L4：MIPS ExcCode 列表（如 `[6,7]` 总线错误）；默认空 |
| `debug` | bool | 输出每条 block/trace 明细（日志会很大） |
| `wait_snapshot_restore` | int | `0x02` ack 前等待（ms） |
| `wait_program_done` | int | `0x03` handler 等待（ms） |
| `elf_path` | string | 仅 Linux 模式使用 |
| `entry_addr` / `entry_code` | hex / array | 仅 Linux 模式使用 |

### 6.2 M9 / M7 地址

| 项 | M9 | M7（`C2900-UN.150.BIN`） |
|---|---|---|
| 外层 ELF `e_machine` | `0xC2`（需修补副本） | `0x0008`（可直接 `-kernel`） |
| `.text` 链接地址 | `0x81000000` | `0x81000000` |
| `.text` 大小 | `0x4956000` | `0x4950000` |
| `.text` 物理范围 | `0x01000000–0x05956000` | `0x01000000–0x05950000` |
| 运行时 text | `0x21xxxxxx` | `0x21xxxxxx` |
| 运行时 data | `0x25xxxxxx` | `0x25xxxxxx` |
| `runtime_delta` | `0x60000000` | `0x60000000` |

### 6.3 `snapshot_device`（可选）

默认 `save_snapshot` 作用于所有块设备，依赖 `-snapshot` 的 qcow2 overlay。
如果不想用 `-snapshot`，可以：

1. 为 QEMU 增加一个专用 qcow2 盘：

   ```bash
   qemu-img create -f qcow2 /tmp/c2900-fuzzstate.qcow2 64M
   # 启动参数追加：
   # -drive if=none,id=fuzzstate,format=qcow2,file=/tmp/c2900-fuzzstate.qcow2
   ```

2. 配置 `"snapshot_device": "fuzzstate"`。

`save_snapshot/load_snapshot` 会只对该设备做内部快照，VM state 写在其中。
该路径已实现，尚未在 C2900 上实测（原型使用 `-snapshot`）。

---

## 7. forkserver 协议与 AFLNet 对接

### 7.1 通道

| 通道 | 路径/键 | 方向 | 用途 |
|---|---|---|---|
| 控制 FIFO | `/tmp/qemu_fuzz_ctl` | fuzzer → QEMU | 1 字节命令 |
| 状态 FIFO | `/tmp/qemu_fuzz_st` | QEMU → fuzzer | 4 字节 status（1 成功 / 2 失败） |
| 自动快照 FIFO | `/tmp/qemu_fuzz_auto` | QEMU 内部 | `auto_snapshot` 用；原型未用 |
| edge bitmap | shm `0x2000` | QEMU → fuzzer | 65536 B AFL bitmap |
| exit status | shm `0x2001` | QEMU → fuzzer | 4 B；原型恒为 `0xDEADBEEF` |

### 7.2 命令

| 命令 | 行为 |
|---|---|
| `0x01` | `save_snapshot("fuzz_snapshot")`；dump 当前覆盖率；备份 baseline |
| `0x02` | 重置覆盖率（IOS 默认清空）→ `load_snapshot` → `vm_start` → 等待 `wait_snapshot_restore` ms → ack |
| `0x03` | 等待 `wait_program_done` ms → dump 本轮覆盖率 → ack |

QEMU 的 fd handler 不在主循环里阻塞等待；等待用 `QEMUTimer` 完成，保证
VM 在等待期间继续执行。

### 7.3 AFLNet `-Z`

AFLNet 的 `init_forkserver_qemu()` / `run_target()` 只依赖上述 FIFO + shm：

```text
AFLNet 启动
  → 发 0x01（保存快照）
  → 每轮：memset(trace_bits) → 发 0x02 → 等 ack
          → 通过真实网络发包/收包
          → 发 0x03 → 等 ack → 读取 bitmap
```

因此 **AFLNet 侧不需要改协议**。当前还差：

1. **快照点**：需要先把 C2900 带到 HTTP 服务就绪，再启动 AFLNet
   （AFLNet 一启动就发 0x01）；
2. **网络通路**：宿主构建未编入 libslirp，原型用 `-nic socket`；
   要跑 AFLNet 需安装 `libslirp-dev` 重建 `-nic user`，或使用 tap（sudo）；
3. **时序**：`-t` 必须大于 `wait_snapshot_restore + 网络往返 + wait_program_done`；
4. **服务入口/代码范围**：建议标定 HTTP 服务代码后缩小 `coverage_start/end`。

参考命令（**网络就绪后**再执行）：

```bash
vendor/aflnet/afl-fuzz -Z -d \
  -i <seeds> -o <out> -m none \
  -N tcp://172.16.100.2/80 -P HTTP \
  -t 10000 -D 1500 -W 500 -- dummy
```

> 当前原型不能直接跑这条命令：guest 网络尚未接通、HTTP 未在快照点启用。
> 这是下一步工作，不是协议不兼容。

### 7.4 退出状态

默认 `crash_mode=off` 时 `0x2001` 始终为 `0xDEADBEEF`，AFLNet 读取后视为
“本轮未退出/超时”。启用崩溃判定后，命中会写入 waitpid 格式状态，例如
`11`（SIGSEGV）或 `23`（IOS software forced crash），AFLNet 直接按
`WIFSIGNALED` 判 `FAULT_CRASH`。

### 7.5 崩溃判定 v1（反馈到同一个 forkserver）

复用了覆盖率 TB 出口和现有的 shm `0x2001` 通道，不需要 AFLNet 改动：

| 层 | QEMU 位置 | 配置 |
|---|---|---|
| L0 TB 哨兵 | `fuzz/coverage.c` 的 `fuzz_coverage_record_tb()` | `panic_addr` / `panic_addrs` |
| L4 致命异常 | `target/mips/tcg/sysemu/tlb_helper.c` 的 `mips_cpu_do_interrupt()` | `crash_exceptions` |
| L1 guest reset | `system/runstate.c` 的 `qemu_system_reset()` | `crash_on_reset` |

模式：

```json
"crash_mode": "off",       // off / log / on
"panic_addrs": [],
"crash_status": 11,
"crash_on_reset": false,
"crash_exceptions": []
```

- `log`：只写 `crash candidate` 日志，不改 shm（用于标定）；
- `on`：第一次命中写 shm `0x2001`，同一轮不覆盖；
- `0x02` reset 时清空本轮崩溃状态并把 shm 恢复为 `0xDEADBEEF`。

已用合成哨兵实测（`scripts/verify-crash.sh`）：

```text
paused: exit=0xdeadbeef
crash_mode=on，panic_addrs=["0x81000000"]，cont 后：
  crash candidate: reason=panic_addr pc=0xffffffff81000000 mode=on status=0x0000000b
  exit=0x0000000b
crash_mode=log：crash candidate 记录，exit 仍为 0xdeadbeef
```

真实 `afl-fuzz -Z` 用同一 sentinel 配置会在 dry run 直接报：

```text
Oops, the program crashed with one of the test cases provided.
[-] PROGRAM ABORT : Test case 'id:000000,orig:req' results in a crash
```

说明 waitpid 状态已被 AFLNet 正确判为 crash。

**真实 IOS 崩溃的标定方法**（尚未在 C2900 上执行）：

1. `crash_mode="log"`，先不填哨兵；
2. 用 IOS 自带机制制造已知崩溃（`Software forced crash` / watchdog）；
3. 对比正常轮次与崩溃轮次的 debug block 地址，挑选只在崩溃路径出现的
   TB 地址作为 `panic_addrs`；
4. 或直接用 `crash_exceptions`（如 `[6,7]` 总线错误）做异常级标定；
5. 重跑已知崩溃，确认日志出现 `crash candidate`，再切 `crash_mode="on"`；
6. 用管理员 `reload` 做负例，避免把正常重启误报；
7. 改检测链路后重跑已知崩溃，防止假阴性。

---

## 8. 覆盖率验证与实测结果

### 8.1 正确的判据

本项目用以下证据判断覆盖率“正确”：

1. **bitmap 非零**：banner 时 62K+ 边；
2. **`phys_missing=0`**：所有 TB 都有有效物理页；
3. **范围过滤生效**：`in_range` 与 `out_range` 分开统计，stub/KSEG1 被过滤；
4. **别名合并**：同一 canonical 地址被不同 virt 命中时记录 `alias_merge`；
5. **多轮稳定**：`loadvm` 后 TB/edge 回到同一水平；
6. **VM 存活**：monitor `info status` 为 running，PC 在变化。

### 8.2 M9 最终运行（`exclude_baseline=true`）

```text
config: range=0x01000000-0x05956000 addr_mode=phys runtime_delta=0x60000000
0x01 save snapshot -> status=1, ~0.74s, bitmap edges=62,144
ios tb stats: seen=7,975,134,389 phys_missing=0
              in_range=7,727,158,809 out_range=247,975,580
              virt[kseg0=0 runtime=6,263,680,614 other=1,711,453,775]
              alias_merge=58,498,990

0x02 loadvm -> status=1, ~1.4s, edges 62,144 -> 85
0x03 end -> status=1, ~1.0s, edges=85
round 1: 85 / round 2: 86 / round 3: 86
verify: stable=yes
```

### 8.3 M7 对照（`exclude_baseline=false`）

```text
banner: 138,505 TB / 61,987 edges
每轮恢复 baseline 后：138,505 TB / 61,987–61,988 edges
```

该对照证明恢复 baseline + `prev_loc_exec` 重置后，每轮完全可复现。

### 8.4 日志中的关键证据

```text
alias merge #1: canon=0x000000000111c704 virt 0xffffffff8111c704 -> 0x000000002111c704
...
alias merge #8: canon=0x000000000111c798 virt 0xffffffff8111c798 -> 0x000000002111c798
```

（上面是 M7 日志样例；M9 的对应样例为 `canon=0x1126584`，
`0xffffffff81126584 -> 0x21126584`。）

- `0xffffffff8111c704`（符号扩展 KSEG0）与 `0x2111c704`（运行时）合并为
  `0x0111c704`（物理）；
- `kseg0=0` 说明 IOS 运行期实际不走低 32 位 KSEG0 别名，不能按
  “只保留 0x8xxxxxxx”硬过滤；
- `out_range≈248M` 是启动 stub/KSEG1 等 `.text` 外执行。

### 8.5 容量

- 64K AFL bitmap 在 boot 后接近饱和（62K/65K）；
- `exclude_baseline=true` 使每轮只占 ~85 条边；
- 如果后续发现目标代码很少、需要更多边，可：
  - 标定 HTTP 服务代码范围，缩小 `coverage_start/end`；
  - 降低 `inst_ratio`（如 16384–32768）；
  - 或扩大 bitmap（需同时改 AFLNet 侧，当前固定 64K）。

### 8.6 如何自己验证覆盖率（三层证据）

#### 第 1 层：覆盖率确实来自 guest 实时执行

```bash
./scripts/verify-coverage.sh m7
```

脚本用 monitor 做四组对照（实测输出）：

```text
paused:            edges=0 sum=0
cont 3s:           edges=1544 sum=16847 hash=9523...
stop 后 1s / 3s:   hash 完全相同（8aa6... / 8aa6...）
cont 后:           hash 变化（c553...），sum 继续增长
0x01 快照(mid-boot): edges=1544
0x02 loadvm:        edges=14     ← exclude_baseline 清空生效
0x03:               edges=20
```

意义：

- **暂停时 bitmap 严格为 0**：不存在预先写好的“假覆盖率”；
- **cont 后增长**：只有 guest TB 执行才会写；
- **stop 后 hash 冻结**：不是定时器/后台线程周期性伪造；
- **0x02 后骤降**：每轮 `loadvm` + reset 生效，不是跨轮累积。

也可以手动观察：

```bash
./runtime/coverage_probe.py stats
./runtime/coverage_probe.py watch 1
./runtime/coverage_probe.py dump /tmp/cov-a.bin
# guest 运行一会儿后
./runtime/coverage_probe.py dump /tmp/cov-b.bin
./runtime/coverage_probe.py diff /tmp/cov-a.bin /tmp/cov-b.bin
./runtime/coverage_probe.py top 20
```

#### 第 2 层：覆盖率地址对应真实固件代码

IOS 覆盖率的 canonical 地址是物理地址。对 M7 的
`C2900-UN.150.BIN`，地址与文件偏移的关系是：

```text
file_off = phys - 0x01000000 + 0x60
```

例如日志里的 `canon=0x0111c704`：

```bash
python3 - <<'EOF'
phys = 0x0111c704
off = phys - 0x01000000 + 0x60
f = open('vendor/c2900/firmware/C2900-UN.150.BIN', 'rb')
f.seek(off)
print(hex(off), f.read(16).hex())
EOF
# 0x11c764 27bdffe0afb10014afb00010afbf0018
```

解码为 `addiu sp,sp,-32` / `sw s1,20(sp)` / `sw s0,16(sp)` /
`sw ra,24(sp)`，是真实的 MIPS 函数序言；入口 `phys=0x01000000` 对应
文件 offset `0x60` 的 `27bdfeb8`（已知 IOS 入口指纹）。

M9 的外层是压缩 ZIP，需先用 binwalk 解出内层 `C2900-UN.BIN`（见
`（内部笔记，未公开）`），再对解压后的内层镜像用同一公式
（M9 `.text` 大小 `0x4956000`）。

#### 第 3 层：AFLNet 实际读到的就是这份 bitmap

```bash
./scripts/verify-aflnet.sh 25
```

它启动本 QEMU（暂停态）、一个宿主侧 dummy HTTP 服务器，并用真实
`afl-fuzz -Z` 连接。实测：

```text
AFLNet: execs=11 bitmap_cvg=2.36% stability=100.00% paths=1
QEMU:   edges=1545
AFLNet bitmap_cvg 2.36% ≈ 1547 edges；QEMU shm edges=1545
PASS
```

- `bitmap_cvg 2.36% × 65536 ≈ 1547`，与 QEMU shm 的 1545 条边一致；
- `stability=100.00%`：同一输入多轮执行产生相同边集，说明快照/重置/归一化
  稳定；
- `execs=11`：AFLNet 确实完成了 11 个 0x02/0x03 轮次并读取了每轮 bitmap。

> 该测试的网络对端是 dummy HTTP 服务器，只证明 **AFLNet ↔ QEMU forkserver
> ↔ shm** 的链路正确；要证明 C2900 HTTP 服务本身的 fuzz 覆盖率，还需要
> 网络就绪快照（见 §10.2）。

---

## 9. 日志与排障

### 9.1 日志位置

| 文件 | 内容 |
|---|---|
| `runtime/run/serial.log` | guest 串口（启动链、banner） |
| `runtime/run/Logs/coverage.log` | 配置、baseline、每轮 TB/边、IOS 统计、别名合并 |
| `runtime/run/Logs/forkserver.log` | 快照保存/恢复、命令计数、错误 |
| `runtime/run/qemu.log` | QEMU 的 stderr（libmachcore trace、启动错误） |
| `logs/m9-validated/` 等 | 已验证运行的历史日志 |

### 9.2 常见问题

**`Device 'char0' is in use`**

`octeon` 机器会调用 `serial_hd(0)`/`serial_hd(1)`；给 chardev 加 `mux=on`
（启动脚本已处理）。

**`could not load kernel ... incompatible architecture`**

M9 外层 ELF `e_machine=0xC2`。启动脚本会生成副本并把 offset 18 改成 `0x0008`；
如果用自定义启动命令，需自己做同样处理（或直接用 M7 的 `C2900-UN.150.BIN`）。

**`save_snapshot failed: Device '...' is writable but does not support snapshots`**

raw 盘不支持内部快照。使用 `-snapshot`（临时 qcow2 overlay），或在配置里
设置 `snapshot_device` 指向专用 qcow2 盘。

**`load_snapshot failed` / `0x02 status=2`**

看 `forkserver.log` 的错误信息；常见原因：
- 快照未保存（先发 0x01）；
- `save_snapshot` 与 `load_snapshot` 的设备列表不一致（本实现已统一）；
- VM 处于不可迁移状态（设备/non-migratable）。

**串口没有输出**

`-chardev file` 只写文件、不支持输入；QEMU 启动前就存在的输出会写入
`serial.log`。如需要交互式 console，可把 chardev 改成 socket/pty（注意 `mux=on`）。

**`-nic user` 报 “not compiled”**

宿主 QEMU 构建没有 libslirp。安装 `libslirp-dev` 后重新 configure/编译，
或使用 `-nic socket`（原型默认）或 tap（sudo）。

**覆盖率日志边数不涨 / 每轮差异大**

- 确认 `exclude_baseline=true`（IOS 默认）且 `0x02` 被正确 ack；
- 检查 `forkserver.log` 是否有 `snapshot restored`；
- 8 位命中计数会回绕，`edges`（非零字节数）可能 ±1；
- 若每轮边数成倍增长，通常是 `prev_loc_exec` 未重置或地址未归一化。

**启动很慢**

插桩模式禁用了 TB 链跳，M7 ~8 min、M9 ~11.5 min 到 banner 属正常。
日志里 `Self decompressing the image : ###...` 期间 M9 会长时间停留。

**stale FIFO/shm**

正常 `start.sh stop` 会清理；若手工 `kill -9`，可能出现残留。再次 `start` 会
重建 FIFO 并重新 memset shm；也可以手动：

```bash
rm -f /tmp/qemu_fuzz_ctl /tmp/qemu_fuzz_st /tmp/qemu_fuzz_auto
ipcs -m | awk '$1 ~ /0x0000200/ {print $2}' | xargs -r -n1 ipcrm -m
```

### 9.3 monitor

```bash
socat - UNIX-CONNECT:runtime/run/monitor.sock
(qemu) info status
(qemu) info registers
(qemu) info snapshots
```

---

## 10. 已知限制与下一步

### 10.1 限制

1. **崩溃判定 v1 已实现，真实 IOS 标定未完成**：L0 TB 哨兵 / L4 异常 /
   L1 guest reset → shm `0x2001`；已用合成哨兵验证 AFLNet 判 crash。真实
   IOS 崩溃的 `panic_addrs`、`crash_exceptions`、`crash_on_reset` 需要在设备上
   用已知崩溃标定；默认 `crash_mode=off`。
2. **快照点在 banner**：不是 HTTP 服务就绪。`snapshot_trigger` 已实现并
   用于 M9 启动 stub 验证；SSH 的 `service_entry` 已用固件静态分析标定为
   `0x8383e3d8`（见 `runtime/protocols/ssh/config/firmware-analysis.json`
   与 `（内部笔记，未公开）`），
   其他协议仍需按服务标定。
3. **宿主↔guest 数据面未验证**：v2 已让 IOS 枚举 Gi0/0，但只做过
   `-nic socket` 启动，没有 ping/TCP 实测。真实网络 fuzz 前需用 tap（或
   user + libslirp）验证；旧 `soc-eval` 板级阻塞已随 v1 移除。另：宿主构建无
   libslirp，tap 需 sudo。
4. **`libmachcore.so` vmstate 未知**：`savevm/loadvm` 本身成功，但恢复后
   网络/设备状态未验证；本轮无发包，不能证明“恢复后路由器还能正常处理 HTTP”。
5. **M9 外层镜像需要 e_machine 修补副本**（脚本自动处理）。
6. **吞吐低**：每轮 1–1.5 s，boot 8–11.5 min；AFLNet 的 `-t/-D/-W` 需重新标定。
7. **bitmap 容量**：完整 `.text` 覆盖在 boot 时接近 64K 上限；已用
   `exclude_baseline` 缓解，后续仍建议缩小服务范围。

### 10.2 下一步

1. **网络就绪快照**：配置 HTTP 服务，在服务可用且无活动连接时快照；
   用已实现的 `snapshot_trigger` 标定 `service_entry`。
2. **接 AFLNet**：安装 `libslirp-dev` 或配置 tap，`-P HTTP` 跑通全链路，
   调 `-t/-D/-W`。
3. **验证 loadvm 后网络**：恢复后发 HTTP 请求，确认 `libmachcore` 设备状态
   足够一致；若不一致，评估设备 vmstate 或改用外部重初始化。
4. **崩溃判定**：按
   `（内部笔记，未公开）`
   标定 L0 哨兵 / L1 QMP reset / L2 串口横幅 / L3 取证。
5. **缩小覆盖范围/采样**：标定 HTTP 代码后调整 `coverage_start/end` 或
   `inst_ratio`。
6. **持久快照**：验证 `snapshot_device` 路径，避免依赖 `-snapshot` 的临时
   overlay（便于跨进程复用快照）。

---

## 11. 代码索引

### 11.1 QEMU 侧（`vendor/qemu/src`，补丁见 `qemu/patches/`）

| 位置 | 作用 |
|---|---|
| `accel/tcg/cpu-exec.c:170` | fuzz 模式禁用 `CF_NO_GOTO_TB\|CF_NO_GOTO_PTR` |
| `accel/tcg/cpu-exec.c:510` | TB 出口调用 `fuzz_coverage_record_tb()` |
| `fuzz/coverage.c:585` | `fuzz_canonical_addr()`：物理/KSEG0/virt 归一化 |
| `fuzz/coverage.c`（IOS 分支） | 跳过 ASID/user/指纹，按范围过滤；`snapshot_trigger` 命中自动快照 |
| `fuzz/coverage.c:1001` | `exclude_baseline` 的 reset 分支 |
| `fuzz/coverage.c:1143` | IOS 默认 `exclude_baseline=true` |
| `fuzz/coverage.c:338` | `snapshot_name` 配置解析 |
| `fuzz/forkserver.c:167` | 每协议快照名解析（默认 `fuzz_snapshot`） |
| `fuzz/forkserver.c` | 0x01/0x02/0x03、快照设备列表 |
| `include/fuzz/coverage.h` | 配置结构、枚举、接口 |
| `system/vl.c:3497` | `-fuzz` 初始化 |
| `qemu-options.hx:4946` | `-fuzz` 选项 |
| `system/meson.build:41` | 编译 `fuzz/*.c` |
| `include/hw/core/tcg-cpu-ops.h` | `get_asid` / `is_user_mode` 回调 |
| `target/mips/cpu.c` | MIPS ASID/用户态实现 |
| `target/mips/tcg/sysemu/tlb_helper.c` | L4：按 ExcCode 掩码记录崩溃候选 |
| `system/runstate.c` | L1：guest reset 记录崩溃候选 |
| `fuzz/coverage.c`（crash 部分） | L0 哨兵/崩溃状态编码到 shm `0x2001` |

### 11.2 C2900 侧（`runtime/`）

| 文件 | 作用 |
|---|---|
| `start_c2900_fuzz.sh` | 启动/停止/状态/等待 banner；M9 e_machine 修补；`-snapshot`；console socket |
| `fuzz_client.py` | save/round/end/verify/stats；shm 读取 |
| `coverage_probe.py` | 独立 shm 探针（stats/dump/diff/watch/top） |
| `c2900-ios.json` | M9 配置 |
| `c2900-ios-m7.json` | M7 配置 |
| `protocols/<proto>/protocol.env` | 协议→配置脚本/快照名/端口/AFLNet 参数 |
| `protocols/<proto>/config/fuzz.json` | 每协议 coverage/snapshot/crash 配置 |
| `../scripts/protocol.sh` | `prepare/configure/status/aflnet-cmd/stop` |
| `../scripts/verify-crash.sh` | L0 哨兵 → shm 0x2001 验证 |
| `test-configs/crash-*.json` | 哨兵/异常/log 模式测试配置 |

### 11.3 关键运行参数

```text
-M octeon -cpu Octeon52XX -m 512
-kernel <M9 外层镜像 / C2900-UN.150.BIN>
-bios firmware/mips64_mixed.bin
-drive id=iomem / pflash / disk0（raw）
-icount shift=1,sleep=off
-nic socket,model=octeon-eth,listen=127.0.0.1:45678
-snapshot
-fuzz runtime/c2900-ios.json
```

---

## 12. 参考资料

项目内与 `project-notes`：

- `qemu/README.md` — 补丁应用与构建
- `logs/README.md` — 验证日志索引
- `runtime/protocols/ssh/config/firmware-analysis.json` — SSH 入口/范围/算法
- `（内部笔记，未公开）`
  — 原型实现/验证/限制
- `（内部笔记，未公开）`
  — 源码改造设计
- `（内部笔记，未公开）` — M9 固件树/启动链/地址
- `（内部笔记，未公开）` — 仿真边界/崩溃自愈/持久化
- `（内部笔记，未公开）`
  — 崩溃判定
- `（内部笔记，未公开）` — SSH 逆向摘要
- `（内部笔记，未公开）` — SSH 流程、
  覆盖率正确性评估、协议适配/分发包摘要与证据

> 项目内原 `docs/` 已移除；删除前备份在
> `<PRIVATE_ASSETS>/C2900SysFuzzer-exp-docs-backup-20261027.tar.gz`。

外部：

- `vendor/qemu/src` — QEMU 8.2.0 C2900 基线（含 `fuzz/` 改造）
- `vendor/aflnet` — AFLNet 2.56b（`-Z` 模式、`-P HTTP/SSH/TLS/SNMP` 等）
- `vendor/c2900` — C2900 仿真包（固件、盘、协议配置脚本）

---

## 13. FAQ

**Q：为什么不用 TCG 插件，而是改 QEMU 源码？**

A：两种路线都可行。插件只能“观测”，无法驱动快照/forkserver，也无法在
IOS 模式下做与 QEMU 内部状态一致的过滤；源码插桩可以直接复用原框架的
TB 出口、`tb_page_addr0()`、`savevm/loadvm` 和 shm 协议。项目已具备可启动
C2900 的 QEMU 源码基线，因此选择源码路线。

**Q：为什么不按 ASID 过滤？**

A：IOS 单地址空间、无 Linux 进程语义；实测运行时 `kseg0=0`、代码主要在
`0x21...`，ASID/用户态过滤会把覆盖率记成空集。

**Q：为什么覆盖率用物理地址而不是虚拟地址？**

A：同一代码有 KSEG0/符号扩展/运行时 TLB 多别名；用虚拟地址会产生多套边。
物理地址是唯一稳定身份，实测 `phys_missing=0`。

**Q：快照会不会改固件文件？**

A：不会。启动脚本使用 `-snapshot`，guest 写入落在临时 qcow2 overlay；
`kernel-elffix.bin` 是 M9 的临时副本，原固件不动。

**Q：AFLNet 现在能跑吗？**

A：协议已兼容，但 guest 网络和 HTTP 服务就绪快照尚未接入；直接跑 AFLNet
会在网络阶段失败。见 §10.2。

**Q：M9 启动为什么这么慢？**

A：插桩禁用 TB 链跳，M9 还要先 stub 解压 131MB 内层镜像；11.5 min 是
当前实测值。M7 已解压镜像约 8 min。后续可考虑 boot 阶段暂时关闭插桩/
只对目标范围记录，或复用持久快照避免每次启动。

**Q：如何加一个新固件/新配置？**

A：复制 `runtime/c2900-ios.json`，按固件 ELF 段头改 `text_size` /
`coverage_start/end`；确认 `e_machine`（非 8 时需修补副本或使用外层镜像）；
先用 `fuzz_client.py save/round/end` 验证覆盖率，再考虑接 AFLNet。

---

## 附录 A：协议字节

```text
ctl FIFO:  1 byte command (0x01/0x02/0x03)
st FIFO:   4 bytes native-endian uint32 (1=success, 2=failure)
edge shm:  key 0x2000, 65536 bytes
exit shm:  key 0x2001, 4 bytes native-endian uint32
snapshot:  name "fuzz_snapshot"
```

## 附录 B：目录迁移说明

原型最初位于 `<仓库根目录>/runtime`，现已在 `runtime/` 内自包含。
QEMU 源码、C2900 固件/配置脚本、AFLNet 都已复制到 `vendor/`，
项目内不再使用指向外部目录的绝对符号链接，可直接打包分发。
