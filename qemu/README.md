# QEMU 插桩改造（补丁）

本目录保存 C2900 原型对 QEMU 源码的改造。**它不是一份完整 QEMU 源码树**，
而是叠加在厂商 `qemu-8.2-eval-v3` 基线上的补丁。基线源码包不公开，
获取方式见 [`../docs/PRIVATE-ASSETS.md`](../docs/PRIVATE-ASSETS.md)。

## 1. 源码基线

| 项 | 值 |
|---|---|
| 私有分发 | `c2900-private-assets/qemu/src/`（v3 已解包，subprojects 已补齐） |
| 工作树 | `vendor/qemu/src`（由私有目录安装，构建时应用本目录补丁，不入库） |
| 版本 | QEMU 8.2.0（厂商 eval 包） |
| 机型 | `-M octeon`（SoC evaluation board，`hw/mips/soc_eval.c`） |
| 设备库 | `roms/machcore/libmachcore.so`（v3，预编译，闭源） |
| 网卡模型 | `octeon-eth`（IOS 可枚举 Gi0/0） |
| 参考实现 | [QemuSysFuzzer](https://github.com/Inuyasha-fan/QemuSysFuzzer)（Malta/Linux，仅作对照） |

> **为什么不是 submodule**：参考项目的 QEMU 基线是公开的 8.2.10 fork，因此可以用
> submodule；本项目基线是厂商 9.0-dev 快照 + Octeon 板级 + 闭源 machcore，
> 上游没有对应树，也不能公开分发，所以只能“私有基线 + 本项目补丁”。

## 2. 补丁内容

```text
qemu/patches/0001-c2900-fuzz-overlay.patch   # 12 个文件，82 KB
```

新增：

| 文件 | 说明 |
|---|---|
| `fuzz/coverage.c` | 覆盖率采集、IOS 模式、物理地址归一化 |
| `fuzz/forkserver.c` | `0x01/0x02/0x03` 快照协议 |
| `include/fuzz/coverage.h` | 配置结构/接口 |
| `include/fuzz/forkserver.h` | FIFO/快照名 |

修改：

| 文件 | 改动 |
|---|---|
| `accel/tcg/cpu-exec.c` | fuzz 模式禁用 TB 链接；TB 出口上报覆盖率 |
| `include/hw/core/tcg-cpu-ops.h` | `get_asid` / `is_user_mode` 回调 |
| `target/mips/cpu.c` | 注册 ASID/用户态回调；补 machcore 需要的 `mips_debug_get_sp()` |
| `target/mips/tcg/sysemu/tlb_helper.c` | L4 致命异常钩子 |
| `system/runstate.c` | L1 guest reset 钩子 |
| `system/vl.c` | `-fuzz` 选项初始化 |
| `qemu-options.hx` | `-fuzz` 帮助文本/枚举 |
| `system/meson.build` | 编译 `fuzz/*.c` |

补丁中 2,134 行是新增源码；其余为对上述 8 个文件的少量修改。补丁由
`vendor/qemu/src` 与干净 v3 源码树逐文件对比生成，已在干净解包上验证
12/12 文件逐字节一致。

## 3. 应用与构建

```bash
# 推荐：整个项目一键（安装 qemu/src + AFLNet + QEMU + 启动检查）
./build.sh

# 方式 A：只安装目录形式的源码并构建
cp -a c2900-private-assets/qemu/src/. vendor/qemu/src/
./scripts/prepare-qemu-src.sh      # 确保 fuzz 补丁已应用
./scripts/build-qemu.sh

# 方式 B：旧格式资产包（目录形式或 tarball 回退）
./scripts/prepare-private-assets.sh /path/to/private-assets

# 方式 C：已有源码树，手动应用补丁
./qemu/apply-patch.sh /path/to/src

# 构建
./scripts/build-qemu.sh            # 默认 vendor/qemu/src
QEMU_SRC=/path/to/src ./scripts/build-qemu.sh
```

`apply-patch.sh` 支持 `--check`、`--revert`，重复执行会跳过已应用状态。

产物：`vendor/qemu/bin/qemu-system-mips64`。运行时还需要：

```bash
LD_LIBRARY_PATH=vendor/qemu/lib:vendor/c2900/lib
```

> **v3 源码包缺陷**：原始 tarball 的 `subprojects/` 不完整（三个只有空
> `.git`，`libvhost-user`/`libvduse` 缺 `include/`）。当前私有分发包中的
> `qemu/src` 已经补齐；只有使用旧 tarball 回退时，才需要
> `qemu-subprojects-fix.tar.gz`（387 KB）。详见 `vendor/qemu/SOURCE.md`。

## 4. IOS 覆盖率模型（摘要）

- `addr_mode=phys`：`canon = tb_page_addr0(tb) | (pc & ~TARGET_PAGE_MASK)`；
  KSEG0 `0x81...`、符号扩展 `0xffffffff81...`、运行时 `0x21/0x25...`
  归一到同一物理 key。
- `coverage_start/end`：`.text` 物理范围。M9 `0x01000000–0x05956000`，
  M7 `.150` `0x01000000–0x05950000`。
- `exclude_baseline=true`（IOS 默认）：每轮 `0x02` 清空 bitmap，
  不写回 boot baseline；否则 64K edge map 在 banner 时就会达到 ~95% 密度。
- `phys_missing`、`virt[kseg0/runtime/other]`、`alias_merge` 统计用于验证
  地址归一化；每次快照/轮次都会写入 `Logs/coverage.log`。

### 崩溃判定 v1

| 层 | 实现 | 配置 |
|---|---|---|
| L0 TB 哨兵 | `fuzz_coverage_record_tb()` 比对 virt/canonical/低 32 位地址 | `panic_addr` / `panic_addrs` |
| L4 致命异常 | `mips_cpu_do_interrupt` 中按 ExcCode 掩码调用 | `crash_exceptions` |
| L1 guest reset | `qemu_system_reset()` 中仅 guest reset 调用 | `crash_on_reset` |

`crash_mode=on` 时把崩溃状态编码成 waitpid 状态写入 shm `0x2001`，
AFLNet 直接按 `WIFSIGNALED` 判 crash。真实 IOS 崩溃地址/掩码仍需标定。

## 5. 与参考实现的差异

[QemuSysFuzzer](https://github.com/Inuyasha-fan/QemuSysFuzzer) 的 `fuzz/`
面向 Linux 用户态：依赖 ASID、用户态标志、ELF 入口指纹、ASLR 偏移与
`.text` 物理范围。本补丁保留其 TB/edge/forkserver 骨架，重写了目标识别
与地址模型；Linux 模式仍然可用（`mode 0/1/2`），IOS 模式用
`mode=3` / `target_type=ios`。
