# C2900 系统级 Fuzz 原型（运行时）

本目录是 C2900 IOS 接入 QemuSysFuzzer 插桩/覆盖率框架的原型运行时，
基于项目内 `../vendor/qemu/src`（`qemu-8.2-eval-v3` + `octeon`/`octeon-eth` + `fuzz/`）。
项目总览、原理、配置与验证结果见上级目录的 [`../README.md`](../README.md)。

> 运行依赖私有 QEMU 基线（`vendor/qemu/src` + `qemu/patches/`）与
> C2900 固件（`vendor/c2900/firmware`），获取方式见
> [`../docs/PRIVATE-ASSETS.md`](../docs/PRIVATE-ASSETS.md)。

当前范围（原型）：**启动 C2900、保存/恢复整机快照、采集 canonical 覆盖率**；
崩溃判定 v1 已实现（L0/L4/L1，合成哨兵验证）；真实 IOS 崩溃标定与
AFLNet 真实协议发包仍待完成。

2026-10-29 验收：v3 machcore 已修复 guest→host TX；M7 + `-fuzz` 启动到
banner 约 23 分钟，`0x01/0x02/0x03` 协议闭环，loadvm 约 1.3 s，
带 ARP 输入连续 3 轮边覆盖 5,868/5,870/5,869，exit status `0xdeadbeef`。
同日完成真实 SSH 验收：tap1 + Paramiko 登录成功，基线 132 边 vs SSH 轮
13,115/13,784 边；L0 哨兵命中后 shm `0x2001=0x0b`。证据：
`../logs/ssh-verify-20261029/`。

## 0. 构建

原型依赖项目内 `../vendor/qemu`（已含 `fuzz/`、`include/fuzz/`、
`accel/tcg/cpu-exec.c` hook、`-fuzz` 选项、`tcg-cpu-ops` 回调）。增量构建：

```bash
../scripts/build-qemu.sh
```

修改清单见 `../qemu/patches/` 与 `../qemu/README.md`。

## 1. 组成

| 文件 | 说明 |
|---|---|
| `c2900-ios.json` | 默认配置：M9 外层镜像，`.text` 物理范围 `0x01000000–0x05956000` |
| `c2900-ios-m7.json` | M7 解压镜像 `C2900-UN.150.BIN`，`.text` 物理范围 `0x01000000–0x05950000` |
| `start_c2900_fuzz.sh` | 启动/停止/状态/等待 banner |
| `fuzz_client.py` | 最小 forkserver 客户端：`save`/`round`/`end`/`verify`/`stats` |
| `coverage_probe.py` | 独立读取 shm：`stats`/`top`/`dump`/`diff`/`watch` |
| `run/` | 通用运行目录：`serial.log`、`qemu.log`、`Logs/coverage.log`、`monitor.sock` |
| `protocols/<proto>/` | 按协议隔离的 configure/config/logs/snapshots/seeds/out（见 `protocols/README.md`） |
| `c2900-program/` | 非网络程序插桩验证（M9 启动 stub + `snapshot_trigger` 自动快照） |

## 2. 启动

```bash
./start_c2900_fuzz.sh start
./start_c2900_fuzz.sh wait 900        # 等待 Press RETURN to get started!
```

默认固件是 `$C2900_PKG/firmware/c2900-universalk9-mz.SPA.150-1.M9.bin`
（默认 `C2900_PKG=<项目>/vendor/c2900`）。M9 外层镜像的 ELF
`e_machine=0xC2`；v2/v3 的 `elf_ops.h` 已放宽为按 MIPS 装载，可直接 `-kernel`。
脚本仍保留“生成 0x0008 兼容副本”的逻辑，仅用于兼容旧 v1 树，无副作用。

使用 M7 解压镜像：

```bash
C2900_KERNEL=../vendor/c2900/firmware/C2900-UN.150.BIN \
C2900_FUZZ_CONFIG=$PWD/c2900-ios-m7.json \
./start_c2900_fuzz.sh start
```

启动参数要点：

- `-M octeon -cpu Octeon52XX -m 512`（v3 板级；可用 `C2900_MACHINE` 覆盖）
- `-kernel <M9 外层镜像或 C2900-UN.150.BIN>`
- `-bios firmware/mips64_mixed.bin`
- 三个 `-drive`：`id=iomem`、pflash、`disk0`
- `-snapshot`：raw 盘用临时 qcow2 overlay，快照可写且不改固件文件
- `-nic socket,model=octeon-eth`：默认 socket；宿主 QEMU 未编入 libslirp，
  真实 AFLNet 发包需改用 tap（`NET_MODE=tap`）或重编带 libslirp 的 QEMU
- `-fuzz runtime/c2900-ios.json`（本目录内即 `c2900-ios.json`）
- 串口写 `run/serial.log`；monitor 在 `run/monitor.sock`

## 2.1 按协议准备（推荐）

```bash
../scripts/protocol.sh list
../scripts/protocol.sh prepare http   # 启动→banner→configure.py→自动 0x01 快照
../scripts/protocol.sh configure http # 已运行实例重跑配置→0x01
../scripts/protocol.sh status http
../scripts/protocol.sh stop http
```

每个协议目录有独立的 `protocol.env`、`configure.py`、`config/fuzz.json`、
`logs/`、`snapshots/`、`seeds/`、`out/`。详见 `protocols/README.md`。

## 2.2 非网络程序插桩验证（M9 启动 stub）

`c2900-program/` 直接在 C2900 仿真内部验证一个特定非网络程序：M9 外层
镜像的启动解压 stub（VA `0x81000000`，物理 `0x01000000`）。它通过
`snapshot_trigger` 在 stub 入口自动 savevm，随后每轮 `0x02/0x03`
loadvm 复现；覆盖率范围严格限定 stub 物理区 `0x01000000-0x01018000`。
全程 `-nic none`，不需要 tap/sudo。

```bash
../scripts/build-qemu.sh                  # 含 snapshot_trigger 实现
../scripts/verify-c2900-program.sh        # 默认 5 轮
../scripts/verify-c2900-program.sh --build --rounds 5
```

实测 5 轮边数 26/27/27/27/27，TB 集 23 个且每轮完全相同，所有地址都在
stub 区；热点校验/累加循环执行 700 万次以上。结果写入
`c2900-program/runs/<时间>-stub-m9/result.json`。该空间与 C2900 runtime
共用 `/tmp/qemu_fuzz_*` 和 shm key，**不能并行运行**。详见
`c2900-program/README.md`（含 monlib 负结果与主 `.text` CLI 差分）。

## 3. 快照与新轮次

等 banner 后：

```bash
./fuzz_client.py save      # 0x01：保存快照；当前覆盖率写入日志并作为参考
./fuzz_client.py round     # 0x02：loadvm + 清零覆盖率 + vm_start
./fuzz_client.py end       # 0x03：等待 wait_program_done 后输出本轮覆盖率
./fuzz_client.py verify --rounds 3
```

协议与 QemuSysFuzzer/AFLNet `-Z` 完全一致：

- `/tmp/qemu_fuzz_ctl`：0x01/0x02/0x03
- `/tmp/qemu_fuzz_st`：4 字节 status
- shm `0x2000`：65536 B edge bitmap
- shm `0x2001`：exit status（原型恒为 `0xDEADBEEF`）

AFLNet 侧无需改协议；但 **AFLNet 启动时会立即发 0x01**，所以必须先让 C2900
到达想要的快照点（当前是 banner / 未来是 HTTP 服务就绪），再启动 AFLNet。

## 4. 配置字段（IOS 模式）

```json
{
    "mode": 3,
    "target_type": "ios",
    "addr_mode": "phys",
    "coverage_types": ["block", "edge"],
    "exclude_baseline": true,
    "inst_ratio": 65536,
    "coverage_start": "0x01000000",
    "coverage_end": "0x05956000",
    "text_start": "0x81000000",
    "text_size": "0x4956000",
    "runtime_delta": "0x60000000",
    "firmware_entry": "0x81000000",
    "wait_snapshot_restore": 1000,
    "wait_program_done": 1000
}
```

- `mode=3` / `target_type=ios`：跳过 Linux 目标识别（ASID/用户态/ELF 指纹）。
- `addr_mode=phys`：用 `tb_page_addr0()` 的物理地址做 canonical key；KSEG0
  `0x81...`、运行时 `0x21/0x25...`、符号扩展地址会落到同一条记录。
- `coverage_start/end`：canonical 过滤范围；C2900 的 `.text` 物理范围。
- `exclude_baseline=true`：每轮 `0x02` 从空 bitmap 开始，不把 boot/CLI 的
  62K 边写回 64K map；否则 map density 接近 100%，fuzzer 没有空间记录新边。
- `inst_ratio`：AFL 风格采样比例；64K map 接近饱和时可用 16384–32768。
- `snapshot_device`（可选）：设置后只对该块设备做内部快照；未设置时
  `save_snapshot` 作用于所有块设备（依赖 `-snapshot` 的 qcow2 overlay）。

## 5. 观测与排障

```bash
tail -f run/serial.log              # guest 串口
tail -f run/Logs/coverage.log       # 配置、baseline、每轮 TB/边、IOS 统计
tail -f run/Logs/forkserver.log     # 快照保存/恢复、命令计数
socat - UNIX-CONNECT:run/monitor.sock   # info status / info registers
./fuzz_client.py stats              # 当前 shm 边数与 exit status
./coverage_probe.py stats           # 同上（独立探针）
./coverage_probe.py watch 1         # 每秒打印 bitmap 统计
./coverage_probe.py dump /tmp/c.bin # 导出 65536 字节 bitmap
./coverage_probe.py diff /tmp/a.bin /tmp/b.bin  # 新增/消失/变化字节
```

覆盖率真实性验证：

```bash
../scripts/verify-coverage.sh m7    # 暂停=0 / cont 增长 / stop 冻结 / loadvm 重置
../scripts/verify-aflnet.sh 25      # AFLNet 读取 bitmap 与 shm 对照
```

`coverage.log` 中的关键证据：

```text
alias merge #1: canon=0x000000000111c704 virt 0xffffffff8111c704 -> 0x000000002111c704
ios tb stats: seen=... phys_missing=0 in_range=... out_range=...
              virt[kseg0=... runtime=... other=...] alias_merge=...
```

## 6. 已知限制

1. 宿主构建没有 libslirp，`-nic user` 不可用；原型用 `-nic socket`。
   已用 raw ARP 帧验证宿主↔guest 双向；真实 HTTP/TCP 协议发包需安装
   `libslirp-dev` 重建，或使用 tap（sudo）。
2. `libmachcore.so` 是预编译设备库，vmstate 覆盖未知；`savevm/loadvm` 实测
   成功，2026-10-29 已验证恢复后 guest 仍能回 ARP（连续 3 轮）。
3. 按协议准备已实现（`../scripts/protocol.sh prepare <proto>`）：配置脚本成功后
   自动保存该协议快照。`snapshot_trigger` 已实现并用于 M9 stub 验证；
   `service_entry` 仍未按服务标定；覆盖率范围
   目前是完整 `.text`。
4. 崩溃/退出状态未实现（本阶段范围外）。
5. 插桩启动明显变慢：2026-10-29 实测 M7 到 banner 约 23 分钟（宿主还有
   其它 TCG 负载时会到 25 分钟量级）；每轮 `0x02`/`0x03` 约 1–1.5 s。
   默认 `exclude_baseline=true` 时，banner 快照后无输入轮次约
   76 TB / 85 条边；注入 ARP 后约 5,420 TB / 5,869 条边
   （整机 boot baseline 约 153,132 TB / 62,811 条边）。
