# C2900SysFuzzer

Cisco C2900 IOS（MIPS64 Octeon）在 QEMU 系统级仿真下的**覆盖率引导 fuzz 原型**。

本项目把 [QemuSysFuzzer](https://github.com/Inuyasha-fan/QemuSysFuzzer)
的 TB 插桩、AFL edge bitmap、快照型 forkserver 迁移到 C2900 IOS 固件，
并在真实固件上完成：

- 带插桩启动 IOS 到 `Press RETURN to get started!`；
- `savevm/loadvm` 整机快照与每轮覆盖率重置；
- 物理地址归一化的 canonical 覆盖率（KSEG0 / 运行时 `0x21` / 符号扩展别名合并）；
- 与 AFLNet `-Z` 相同的 FIFO + shm 协议；
- 真实 SSH 登录与 AFLNet campaign（见 [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md)）。

> 当前是**原型**：快照、覆盖率、forkserver 协议已跑通；崩溃判定 v1
> （L0/L4/L1 → shm `0x2001`）已用合成哨兵验证，真实 IOS 崩溃标定仍未完成。

---

## ⚠️ 私有资产（必读）

本仓库**不包含**固件、厂商 QEMU 源码/设备库。请向维护者私下获取一个合并的
**目录形式**资产包（QEMU 源码目录 + 固件侧）：

```text
c2900-private-assets/
├── qemu/src/                # qemu-8.2-eval-v3 已解包（subprojects 已补齐）
├── qemu/lib/libmachcore.so  # v3 闭源设备库
└── c2900/firmware/ + lib/   # 固件/磁盘镜像 + 运行库
```

拿到后把整个目录复制到仓库的 `vendor/`，然后运行构建脚本：

```bash
cp -a /path/to/c2900-private-assets vendor/
./build.sh
```

`qemu/src/` 是未应用 C2900 fuzz 补丁的厂商基线；`build.sh` 会复制到
`vendor/qemu/src/` 后应用 `qemu/patches/`。`build.sh` 只做结构检查
（`qemu/src/VERSION`、subprojects、固件是否存在），不附带/校验哈希清单。
获取方式与单文件哈希见 [`docs/PRIVATE-ASSETS.md`](docs/PRIVATE-ASSETS.md)。

**不要**把资产目录或固件/设备库提交到 GitHub、Release 或 LFS。
`scripts/check-no-private.sh` 用于防止误提交。

## 仓库组成

| 路径 | 内容 |
|---|---|
| `build.sh` | **一键构建**：安装资产 → 拉取/编译 AFLNet → 构建 QEMU → 启动检查 → 打印待填配置 |
| `qemu/patches/0001-c2900-fuzz-overlay.patch` | QEMU TB 插桩/覆盖率/forkserver 补丁（12 个文件，82 KB） |
| `qemu/apply-patch.sh` | 应用/检查/回滚 QEMU 补丁（幂等） |
| `vendor/aflnet/` | **官方** AFLNet submodule（`aflnet/aflnet`，钉在 `96032f8`） |
| `patches/aflnet.patch` | 给官方 AFLNet 增加 `-Z` 外部 QEMU 系统模式支持 |
| `runtime/` | 启动脚本、配置、协议目录、种子、覆盖率工具 |
| `scripts/` | 资产/构建/检查/验证脚本 |
| `tools/coverage2.c` | 可选的 TCG 覆盖率插件源码 |
| `docs/` | 架构、私有资产、验收记录 |
| `logs/` | 关键验收日志摘要 |
| `fw_probe/` | 固件探测辅助脚本 |

QEMU 的改造以**补丁**而不是整棵源码树的形式提供，原因见
[`qemu/README.md`](qemu/README.md)：基线是厂商私有树，无法像
QemuSysFuzzer 那样用公开 QEMU submodule。AFLNet 使用官方仓库，
`-Z` 支持通过 `patches/aflnet.patch` 在构建时应用。

## 环境要求

- Linux x86_64（Ubuntu 22.04 验证；QEMU 二进制需要 GLIBC 2.34+）；
- bash、git、make、patch、tar、tmux、socat、python3、ninja、cc、pkg-config；
  （`zstd` 仅在你自己把资产目录压成 `.tar.zst` 后需要）
- 拉取 AFLNet submodule 需要网络；
- 构建依赖（Ubuntu 22.04）：

  ```bash
  sudo apt install build-essential ninja-build pkg-config \
                   libglib2.0-dev libgraphviz-dev libcap-dev \
                   zstd git make patch tmux socat python3
  ```

- 协议测试需要 Paramiko / tlslite-ng / strongSwan 等，见
  `vendor/c2900/test/download_deps.sh` 与各协议 README；
- 创建 tap 需要 `sudo`（基础 socket fuzz 不需要）。

## 快速开始

### 1. 克隆

```bash
git clone --recursive <本仓库 URL> C2900SysFuzzer
cd C2900SysFuzzer

# 忘了 --recursive 也可以，build.sh 会补：
# git submodule update --init --recursive
```

### 2. 放入私有资产并构建

```bash
cp -a /path/to/c2900-private-assets vendor/
./build.sh
```

`build.sh` 依次完成：

1. 校验并安装 `vendor/*private-assets*/`（`qemu/src`、`machcore`、固件、运行库）；
2. 拉取**官方** `aflnet/aflnet`（submodule，固定 commit），应用
   `patches/aflnet.patch`，编译 `vendor/aflnet/afl-fuzz`；
3. 安装 `qemu/src`、应用 fuzz 补丁并构建
   `vendor/qemu/bin/qemu-system-mips64`；
4. 执行 `check-assets.sh`、`check-env.sh`，启动脚本 smoke test
   （默认暂停态启动/停止，几秒钟；`--full` 可等 IOS banner），
   最后在终端打印需要手动确认/填写的配置位置。

常用参数：

```bash
./build.sh --skip-smoke     # 跳过启动检查
./build.sh --full           # 启动到 banner（约 20 分钟）后再停止
./build.sh --force-assets   # 强制重新安装资产包（含重装 qemu/src）
```

### 3. 启动 fuzz

```bash
./runtime/start_c2900_fuzz.sh start
./runtime/start_c2900_fuzz.sh wait 1800     # 等 IOS banner（插桩启动较慢）
python3 runtime/fuzz_client.py save         # 0x01
python3 runtime/fuzz_client.py round        # 0x02
python3 runtime/fuzz_client.py end          # 0x03
```

协议级 campaign（以 SSH 为例，需要先准备 tap）：

```bash
sudo ./scripts/setup-tap.sh tap1
cd runtime/protocols/ssh
KIND=m7 TAP_IFACE=tap1 ./campaign.sh start   # banner → SSH 就绪 → 0x01
./campaign.sh aflnet                         # 前台 AFLNet -Z
./campaign.sh stop
```

各协议配置与验证见 `runtime/protocols/<proto>/README.md`。

> 如果已经手动安装好 `vendor/qemu/src`、固件和设备库，也可以只跑
> `./scripts/build-qemu.sh`；若不想把私有文件放进仓库，可用
> `C2900_PKG` / `C2900_QEMU` / `C2900_QEMU_LIB` / `QEMU_SRC` 指向外部。

## 原理概览

```text
guest 指令
  → TCG 翻译成 TB
  → [fuzz 模式] 禁用 goto_tb 直接链跳
  → 每个 TB 执行后回调 fuzz_coverage_record_tb(pc, phys, ...)
  → edge_bitmap[cur ^ prev]++
  → shm 0x2000（AFL 兼容 64K bitmap）
  → forkserver：0x01 savevm / 0x02 loadvm+reset / 0x03 end round
  → AFLNet 直接读取 bitmap 与 exit status（shm 0x2001）
```

- **TB 粒度**：与基本块对应，统一在 `cpu_tb_exec()` 出口记录；
- **地址归一化**：以物理地址为 key，合并 KSEG0/符号扩展/运行时别名；
- **状态重置**：整机 RAM/CPU/设备 `loadvm`，比 fork 更彻底。

细节见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) 与
[`qemu/README.md`](qemu/README.md)。

## 实测状态

| 项 | 结果 |
|---|---|
| M7 插桩启动到 banner | 约 23 min |
| `0x01` / `0x02` / `0x03` | 快照约 0.5–0.7 s；loadvm 约 1.3 s；正常结束 |
| 每轮覆盖率（ARP 输入） | 5,868 / 5,870 / 5,869 边 |
| 真实 SSH 登录轮 | 13,115 / 13,784 边（基线 132） |
| AFLNet SSH 60 s 试跑 | 9 execs，bitmap_cvg 13.48%，stability 100% |
| 崩溃判定 | 合成哨兵通过；真实 IOS 未标定 |

完整数据与日志见 [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md)。

## 已知限制

- 插桩使启动/执行显著变慢（banner 分钟级），尚无性能调优；
- `crash_mode` 默认 `off`；真实崩溃地址/异常掩码待标定；
- 仅支持厂商 `qemu-8.2-eval-v2/v3` 基线，不能套用到上游普通 QEMU；
- 私有资产不在仓库内，clone 后不能直接运行。

## 文档

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 原理、协议、地址模型与实测细节
- [`docs/PRIVATE-ASSETS.md`](docs/PRIVATE-ASSETS.md) — 私有资产清单与 SHA256
- [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) — 验收记录摘要
- [`docs/PUBLISHING.md`](docs/PUBLISHING.md) — 发布到 GitHub 的步骤

## 相关项目

- [QemuSysFuzzer](https://github.com/Inuyasha-fan/QemuSysFuzzer) — TB 插桩/forkserver 框架来源；
- [AFLNet](https://github.com/aflnet/aflnet) — 官方网络协议 fuzz 仓库；
  本项目在 `96032f8` 上应用 [`patches/aflnet.patch`](patches/aflnet.patch)
  以获得 `-Z` 外部 QEMU 系统模式支持；
- [QEMU](https://www.qemu.org/)。

## 许可与合规

第三方组件许可见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。Cisco 固件与厂商
QEMU eval 包**不在本仓库中**，也不构成任何再分发授权。
