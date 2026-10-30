# C2900 非网络程序插桩测试空间（M9 启动 stub）

本目录不再另起 Linux 客体，而是**直接在能启动的 C2900 IOS 仿真上**选一个
特定的**非网络程序**，用插桩 QEMU + 快照恢复机制做严格验证。

选定目标：**M9 外层镜像的启动解压 stub**

```text
M9 外层 .bin
├── ELF32 BE, entry 0x81000000
├── PT_LOAD off=0x194, vaddr=0x81000000, 0x18000 B   ← 本目录验证的 stub
├── FEEDFACE + ZIP（C2900-UN.BIN，131,343,092 B）
└── NOTE
```

stub 在 IOS 之前运行：保存寄存器/参数、探测内存、设置栈，调用
`0x81002a50`（解包/加载主流程），使用 ROM `syscall` 读取/输出，解压
内层 IOS 后跳入 `0x81000000`。它不依赖网络、不依赖 IOS CLI，是固件树里
一个边界明确的非网络程序（详见
`（内部笔记，未公开）`）。

## 1. 验证链路

```text
项目 qemu-system-mips64（M9 外层镜像，-nic none）
  → ROM 微码在 0x81000000 启动 stub
  → fuzz/coverage.c 命中 snapshot_trigger=0x81000000
  → /tmp/qemu_fuzz_auto 通知 forkserver 自动 savevm（snap_stub）
  → 每轮 0x02 loadvm 回到 stub 入口，0x03 收集本轮到覆盖率
  → 覆盖率范围严格限定 stub 物理区 0x01000000-0x01018000
  → debug block 日志给出 TB 地址/执行次数，可映射回外层镜像文件偏移
```

为此在 fuzz 框架里实现了原本只解析、未实现的 `snapshot_trigger`：

- 位置：`vendor/qemu/src/fuzz/coverage.c`（IOS 分支，TB 记录入口）；
- 命中条件：`virt` 低 32 位 / `phys` / canonical 任一等于配置值；
- 动作：向自动快照管道写 1 字节，forkserver 用配置的 `snapshot_name`
  保存快照；每个 QEMU 进程只触发一次，`loadvm` 回到触发点不会重复存快照。

> **必须用新源码重建 QEMU**：`./scripts/build-qemu.sh`（或
> `./scripts/verify-c2900-program.sh --build`）。旧的预编译二进制没有
> `snapshot_trigger` 实现，验证会卡在等待触发。

## 2. 快速开始

```bash
cd <仓库根目录>

./scripts/build-qemu.sh                 # 源码含 snapshot_trigger
./scripts/verify-c2900-program.sh       # 默认 5 轮
# 或
./scripts/verify-c2900-program.sh --rounds 3
```

脚本使用：

- `runtime/c2900-program/config/stub-snapshot-m9.json`
- `C2900_NIC_ARGS=none`（QEMU `-nic none`，不建网卡/tap）
- `runtime/start_c2900_fuzz.sh` 启动 M9（自动生成 e_machine 修补副本）
- `runtime/fuzz_client.py` 发送 `0x02/0x03`

结果在 `runtime/c2900-program/runs/<时间>-stub-m9/`：

```text
result.json              # 触发信息、每轮边数、TB 集、PASS/FAIL、哈希
Logs/coverage.log        # snapshot_trigger + debug block 明细
Logs/forkserver.log      # 自动快照、每轮 loadvm
client-round-*.log       # 0x02 输出
client-end-*.log         # 0x03 输出
serial.log / qemu.log
```

常用参数：

```text
--rounds N              轮数（默认 5）
--firmware PATH         M9 外层镜像（默认 vendor/c2900/firmware/...M9.bin）
--qemu PATH             覆盖 QEMU 二进制
--trigger-timeout SEC   等待 stub 触发/自动快照（默认 120）
--run-dir DIR           指定输出目录
```

## 3. PASS 判据

| 检查 | 判据 |
|---|---|
| `trigger_hit` | 触发点 `virt=0xffffffff81000000`、`phys=0x01000000` |
| `auto_snapshot_saved` | forkserver 日志出现 `snapshot saved: snap_stub` |
| `all_rounds_status_ok` | 每轮 `0x02`/`0x03` status=1 |
| `edges_nonzero` | 每轮边覆盖 >0 |
| `edges_stable` | 轮次边数差 ≤ max(4, 最大边数/10) |
| `tb_sets_identical` | 各轮 debug TB 集完全相同 |
| `tb_in_stub_range` | 所有 TB 地址在 `0x01000000-0x01018000` |
| `hot_loop_seen` | 至少一个 TB 的 exec_count ≥ 1000（stub 主循环） |

轮内 timing 参数：`wait_snapshot_restore=100ms`、`wait_program_done=300ms`。
0.3s 窗口足以覆盖入口初始化与主循环开头，又不会进入被解压后的内层 IOS，
因此覆盖率是**纯 stub**。循环迭代次数随宿主调度波动（`bitmap_sum` 会变），
但执行的 TB 集稳定。

## 4. 实测结果（2026-10-20，M9）

启动后数秒到十几秒触发（首次实测约 11 秒）：

```text
snapshot trigger hit: virt=0xffffffff81000000 phys=0x0000000001000000
                      canon=0x0000000001000000 target=0x0000000081000000
snapshot saved: snap_stub
```

5 轮（100ms + 300ms 窗口）：

| 轮 | 0x02 loadvm | 0x03 边数 | bitmap_sum | TB 数 |
|---|---|---|---|---|
| 1 | status=1, 0.21s | 26 | 259 | 23 |
| 2 | status=1, 0.22s | 27 | 103 | 23 |
| 3 | status=1, 0.22s | 27 | 230 | 23 |
| 4 | status=1, 0.22s | 27 | 164 | 23 |
| 5 | status=1, 0.22s | 27 | 82 | 23 |

- TB 集 5 轮完全相同，地址范围 `0x01000090-0x01003e48`；
- 边数 26/27（±1），所有地址都落在 stub 物理区；
- 覆盖到的代码全部是外层 stub，未进入内层 IOS。

热点 TB（第 5 轮，外层文件偏移 = `0x194 + phys - 0x01000000`）：

```text
TB phys=0x01002cb4  outer_off=0x2e48  exec=7,344,051  insn=7
  0x81002cb4: lw  $s0,($a2); addiu $a0,-1; addiu $a2,4
              addu $v1,$a1,$s0; sltu $v0,$v1,$s0
              bne $a0,$a3,0x81002cb4; addu $a1,$v1,$v0
  → 32 位累加/校验循环（stub 处理大块数据）

TB phys=0x010000d4  outer_off=0x268   exec=4
TB phys=0x01003cdc  outer_off=0x3e70  exec=2
```

在更长的 1s 窗口证据中还能看到：

```text
TB phys=0x010026e4  exec=40,674,597  → 反向逐字节拷贝循环（memmove）
TB phys=0x01002cb4  exec=15,661,449  → 上述累加/校验循环
```

入口与关键代码（链接 VA）：

```text
0x81000000  保存 gp/sp/ra/a0-a3/s0-s7/fp 到 0x81018xxx
0x81000088  jal 0x81003d14           ; 内存/环境探测
0x810000b8  jal 0x81002a50           ; 解包/加载主流程
0x810000c0  jal 0x81002864           ; 主流程返回后的处理/输出
0x810000d0  syscall                  ; ROM 服务
0x810002e0  syscall                  ; ROM 服务
```

证据目录：

- `evidence/stub-5round/`：最终 5 轮（Logs、serial、config、result.json）
- `runs/20261020-175849-stub-m9/result.json`：`verify-c2900-program.sh` 的完整结果
- `evidence/stub-1s-window/`：1s 窗口的 5 轮记录（前 3 轮 33/34/34；第 4 轮 93 边离群；第 5 轮 33；可见 0x810026e4 的 4000 万次 memmove）
- `analysis/stub_analysis.py` 可离线复现地址映射与热点反汇编：

  ```bash
  ./runtime/c2900-program/analysis/stub_analysis.py \
    --coverage-log runtime/c2900-program/evidence/stub-5round/Logs/coverage.log
  ```

## 5. 目录

```text
runtime/c2900-program/
├── README.md
├── verify_stub.py                 # 启动→等待触发→多轮→判定→result.json
├── config/
│   ├── stub-snapshot-m9.json      # 主用例：snapshot_trigger=0x81000000
│   ├── maintext-debug-m7.json     # 补充：主 .text CLI 命令差分
│   ├── monlib-kseg0-m7.json       # 补充：monlib KSEG0 范围探测
│   └── monlib-probe-m7.json       # 补充：低物理范围探测
├── analysis/
│   ├── stub_analysis.py           # stub 反汇编 + TB→文件偏移映射（主）
│   ├── diff_rounds.py             # 主 .text 分轮差分（补充）
│   ├── scan_cluster_strings.py    # CLI 差分簇的字符串引用扫描
│   ├── monlib_analysis.py         # 内嵌 monlib ELF/.pdr/函数线索
│   ├── coverage_to_functions.py   # monlib 覆盖率→函数映射
│   └── monlib-m7.json             # monlib 490 个 .pdr 函数分析结果
└── evidence/
    ├── stub-5round/               # 主证据
    ├── stub-1s-window/
    ├── cli-maintext/              # show version/show clock 差分
    ├── monlib-negative/           # dir/format 不执行 monlib 的取证
    └── probe-phys-lowmem/         # 早期低物理范围探测
```

## 6. 补充分析与负结果

这些是在选定 stub 之前的排查，保留作为逆向证据：

- **内嵌 monlib/FFS 模块**：M7/M9 `.rodata` 中同一份 ELF（VA 0x80008000，
  `.text` 0x180a4，490 个 `.pdr` 函数，含 `dfs_openfile`/`dfs_closefile`/
  `Formatting failure`/`Writing Monlib sectors` 等字符串）。但
  `dir flash:` 与 `format flash:` 在 KSEG0 `0x80008000-0x800200a4` 范围内
  都是 **0 TB**，RAM 中也只有固件 `.rodata` 内的一份代码；结论：这两条
  CLI 路径没有执行该模块（它更可能服务于 ROMmon/专用恢复路径）。
  证据：`evidence/monlib-negative/`。
- **主 `.text` CLI 命令差分**（M7，特权提示符快照）：
  `show version` 13,986 边（含一次 TLB 异常 traceback），
  空白对照 149 边，`show clock` 3,379 边，重复 `show version` 13,886 边。
  差分 TB 集覆盖 430 个簇，代码路径分散在整个 IOS `.text`，难以定义
  单一函数边界；因此没有把它作为主用例。证据：`evidence/cli-maintext/`。

## 7. 边界

1. **不能与 C2900 runtime 并行**：共用 `/tmp/qemu_fuzz_ctl`、
   `/tmp/qemu_fuzz_st` 和 shm `0x2000/0x2001`；脚本检测到运行中的
   `vendor/qemu/bin/qemu-system-mips64` 会拒绝。
2. **M9 外层镜像需要 e_machine 修补副本**：`start_c2900_fuzz.sh` 在运行
   目录生成 `kernel-elffix.bin`，原固件不动；停止时删除副本。
3. **快照在 `-snapshot` 临时 overlay 中**，QEMU 停止后失效。
4. **轮内窗口是 0.4s**：只验证 stub 入口/主循环开头，不等完整解压；
   这是为了不进入内层 IOS，不代表完整 stub 路径覆盖率。
5. **`snapshot_trigger` 是单次触发**：每轮 loadvm 回到入口不会重新存快照。
6. 本用例只验证“QEMU 侧插桩 + 快照复现 + 程序范围归属”，不解决 C2900
   板级 Gi0/0/PKO 缺失，也不涉及 IOS 网络服务。
