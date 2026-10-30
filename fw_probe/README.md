# fw_probe — C2900 IOS 固件协议入口/范围定位工具

`fw_probe.py` 是一个**只读固件、不启动 QEMU** 的静态分析工具，用于在
Cisco C2900 IOS 内层镜像（M9/M7 等）中回答下面这些问题：

1. 某个协议的**端口**和**内部 handler 名**是什么？
2. 协议代码在 `.text` 里的**入口**有哪些（监听注册、TCP 回调、报文
   parser、消息分发、进程入口）？
3. 协议子系统的**代码范围**是什么（用于 `fuzz.json` 的
   `coverage_start/coverage_end`）？
4. 服务就绪点/快照触发点（`snapshot_trigger`）应该是哪条指令？
5. 这些地址在 linked / runtime / phys 三种地址空间里分别是什么？

工具面向 QemuSysFuzzer 的协议 config 准备工作，也可以单独用于 IOS
固件逆向。它不修改固件，不发包，不执行固件代码。

- 日期：2026-10-26
- 对应内核：C2900 IOS 15.0(1)M9 / M7
- 详细分析结果：`../runtime/protocols/ssh/config/firmware-analysis.json`
  与 `../runtime/protocols/ssh/analysis/`；摘要见
  `（内部笔记，未公开）`

## 目录

1. [快速开始](#1-快速开始)
2. [为什么需要它](#2-为什么需要它)
3. [目录与运行方式](#3-目录与运行方式)
4. [命令行参考](#4-命令行参考)
5. [工作原理](#5-工作原理)
6. [输出解读](#6-输出解读)
7. [从输出到 config/fuzz.json](#7-从输出到-configfuzzjson)
8. [新增一个协议的推荐流程](#8-新增一个协议的推荐流程)
9. [已分析的 SSH 结果（速查）](#9-已分析的-ssh-结果速查)
10. [常见问题与排错](#10-常见问题与排错)
11. [限制与正确性边界](#11-限制与正确性边界)
12. [相关文件](#12-相关文件)
13. [变更记录](#13-变更记录)

---

## 1. 快速开始

### 1.1 依赖

- Python 3.8+
- [`pyelftools`](https://github.com/eliben/pyelftools)

```bash
# 方式 A：项目内 requirements
python3 -m pip install -r fw_probe/requirements.txt

# 方式 B：直接安装
python3 -m pip install "pyelftools>=0.29"
```

工具**不需要** capstone、Ghidra、radare2；反汇编证据是另外用 capstone
生成的（可选）。

### 1.2 准备内层镜像

外层 `.bin` 是 `ELF(0xC2) + FEEDFACE + ZIP`，工具要的是 ZIP 里的内层
IOS ELF。M9 的权威内层镜像位于 binwalk 提取目录：

```text
/tmp/binwalk_m9/_c2900-universalk9-mz.SPA.150-1.M9.bin.extracted/C2900-UN.BIN
```

M7 的内层镜像已直接放在项目里：

```text
vendor/c2900/firmware/C2900-UN.150.BIN
```

如果是全新的外层镜像，按
`（内部笔记，未公开）` 的 EOCD + `zipfile`
方法解出 `C2900-UN.BIN`。

### 1.3 三条最常用的命令

在**项目根目录**执行（相对路径都相对于项目根目录）：

```bash
# 1) 列出 .data 内部协议定义表：端口 + handler 名（很快，不做函数分析）
python3 fw_probe/fw_probe.py \
  --image /tmp/binwalk_m9/_c2900-universalk9-mz.SPA.150-1.M9.bin.extracted/C2900-UN.BIN \
  --list-services

# 2) 用内置 SSH preset 找入口/范围，输出 JSON
python3 fw_probe/fw_probe.py \
  --image /tmp/binwalk_m9/_c2900-universalk9-mz.SPA.150-1.M9.bin.extracted/C2900-UN.BIN \
  --preset ssh \
  --json /tmp/m9-ssh.json

# 3) 自定义协议：显式给 marker 和端口
python3 fw_probe/fw_probe.py \
  --image <inner> \
  --marker "HTTP-fast-server" --marker "HTTP/1.1" \
  --port 80 --json /tmp/http.json
```

M9 镜像较大（`.text` 约 77 MB），完整分析约 **40 秒**；
`--list-services` 只扫 `.data`，通常 1 秒内完成。

---

## 2. 为什么需要它

### 2.1 IOS 镜像是无符号的单体 ELF

C2900 内层 `C2900-UN.BIN` 是一个 ELF32 BE 镜像：

| 段 | 链接地址 | 说明 |
|---|---|---|
| `.text` | `0x81000000` | IOS 代码（M9 大小 `0x4956000`） |
| `.rodata` | M9 `0x85956000` / M7 `0x85950000` | 字符串、只读表 |
| `.data` | M9 `0x87827090` / M7 `0x8781cf10` | 全局变量、Tcl 协议定义表 |
| `sreloc` | — | 启动时自重定位表 |

镜像没有符号表，协议代码按组件静态链接在一起。要定位“SSH 入口”不能
靠函数名，只能靠字符串、调用模式、全局结构和组件边界。

### 2.2 三种地址：linked / runtime / phys

同一段代码在三个地址空间里各有一个地址：

```text
linked   0x8111c798   ELF 文件里的链接地址
runtime  0x2111c798   linked - 0x60000000（IOS 实际执行/引用数据用这个）
phys     0x0111c798   linked - 0x80000000（QEMU 覆盖率 canonical key）
```

公式：

```text
runtime = linked - 0x60000000      # 由 QEMU 日志和代码实测确认
phys    = linked - 0x80000000      # 等价 linked & 0x1fffffff
```

`fuzz.json` 在 `addr_mode=phys` 下，`coverage_start/coverage_end` 要填
**phys**；`snapshot_trigger` 可以用 runtime（匹配 virt 低 32 位）或 phys。

### 2.3 最关键的一个坑：字符串引用用 runtime 别名

编译器在代码里构造 `.rodata` 字符串地址时，用的是 **runtime 别名**，
不是 linked VA。例如 M9 的 `"SSH Process"`：

```text
字符串 linked VA  0x86b5d2b0
代码中的立即数   0x26b5d2b0   (= 0x86b5d2b0 - 0x60000000)
```

直接全镜像搜 `0x86b5d2b0`：**0 命中**。
搜 `0x26b5d2b0`：立刻命中 `0x838355d0`（SSH 进程创建函数）。

工具对每个字符串同时登记 linked 和 runtime 两个别名，所以能命中。
这是它相对“直接 grep 地址”的核心价值。

---

## 3. 目录与运行方式

```text
C2900SysFuzzer-exp/
├── fw_probe/
│   ├── fw_probe.py        # 本工具（单文件，无第三方依赖除 pyelftools）
│   ├── requirements.txt   # pyelftools
│   └── README.md          # 本文件
├── scripts/
│   ├── gen_ssh_seed.py    # SSH 明文握手种子生成器（配套，可选）
│   └── ...
├── runtime/protocols/ssh/
│   ├── analysis/          # 逐函数反汇编、字符串、probe JSON
│   └── config/firmware-analysis.json
└── vendor/c2900/firmware/
    └── C2900-UN.150.BIN   # M7 内层镜像
```

运行方式：在项目根目录用相对路径执行 `python3 fw_probe/fw_probe.py ...`。
工具不依赖当前目录，但 README/示例中的 `--json` 输出路径是相对项目根
目录写的。

文件权限：`fw_probe.py` 带 shebang，已加执行位；可以用
`python3 fw_probe/fw_probe.py ...`，也可以 `./fw_probe/fw_probe.py ...`。

---

## 4. 命令行参考

```text
usage: fw_probe.py [-h] --image IMAGE [--marker MARKER]
                   [--preset {ftp,http,https,ipsec,snmp,ssh,telnet}]
                   [--list-services] [--ignore-case] [--port PORT]
                   [--delta DELTA] [--merge-gap MERGE_GAP]
                   [--detail-gap DETAIL_GAP] [--json JSON]
                   [--show-context SHOW_CONTEXT]
```

| 选项 | 默认 | 说明 |
|---|---|---|
| `--image` | 必填 | 内层 IOS ELF（`C2900-UN.BIN` 等），不是外层 `.bin` |
| `--marker` | — | 协议特征字符串，可重复。匹配 `.rodata`/`.data` 中包含该子串的字符串 |
| `--preset` | — | 内置协议起点：`ssh`、`http`、`https`、`telnet`、`snmp`、`ipsec`、`ftp`。用 `--marker`/`--port` 可覆盖 |
| `--list-services` | off | 只列出 `.data` 内部 Tcl 协议定义表（端口/handler/protocol-id），不做函数分析 |
| `--ignore-case` | off | marker 大小写不敏感 |
| `--port` | — | 协议端口；工具会找所有 `addiu reg,zero,port` 常量，并标出落在 marker 跨度内的引用 |
| `--delta` | `0x60000000` | runtime 重定位差；C2900 M7/M9 都是 `0x60000000`，其他平台可能不同 |
| `--merge-gap` | `0x8000` | marker 簇合并阈值，用于得到 `marker_span` |
| `--detail-gap` | `0x400` | 明细簇合并阈值，用于输出 `clusters[]` |
| `--json` | — | 把完整结果写到 JSON |
| `--show-context` | `3` | 每个明细簇上下各显示几个函数（文本输出） |
| `-h/--help` | — | 帮助 |

### 4.1 `--list-services`：先拿端口和 handler 名

IOS 的 `.data` 里有一张 Tcl 风格的协议/应用识别定义表（NBAR 风格），
例如 M9：

```text
(define-handler SSH-Server
  (tcp-static-port 22 `ssh' 40 `Secured Shell' SSH-Server))
```

命令：

```bash
python3 fw_probe/fw_probe.py --image <inner> --list-services
```

输出每一行带 `.data` VA，例如：

```text
0x88243cf4  (tcp-static-port 443 `secure-http' 16 `Secured HTTP' HTTPSSL-Server))
0x882449c3  (combined-dual-static-port 161 162 `snmp' 38 `Simple Network Management Protocol'
0x88244ab6  (tcp-static-port 22 `ssh' 40 `Secured Shell' SSH-Server))
0x88244b7c  (tcp-static-port 23 `telnet' 42 `Telnet' TELNET-Server))
0x882452cf  (combined-static-port 500 `isakmp' 94 `Internet Security Association & Key Management Protocol'
```

注意：

- 这张表是 **NBAR/应用识别风格**的协议定义，不是“SSH 服务启动表”。
  服务真正启动的代码仍需第 4.2 步确认。
- handler 名可以作为下一步的 marker，但不保证与真正服务代码同名；
  SSH 同名且能命中，其他协议需要验证。

### 4.2 `--preset` / `--marker`：找代码入口/范围

```bash
# 内置 SSH preset
python3 fw_probe/fw_probe.py --image <inner> --preset ssh --json /tmp/ssh.json

# 等价显式写法
python3 fw_probe/fw_probe.py --image <inner> \
  --marker "SSH Process" --marker "SSH Event handler" \
  --marker "tcp_listen_ssh_port" --marker "SSH-Server" \
  --marker "SSH2_MSG_" --marker "SSH_MSG_" --marker "ssh_receive" \
  --port 22 --json /tmp/ssh.json
```

preset 只是起点；不同 IOS 版本/组件名可能变化，按需增删 marker：

| preset | 端口 | 默认 marker |
|---|---:|---|
| `ssh` | 22 | `SSH Process`、`SSH Event handler`、`tcp_listen_ssh_port`、`SSH-Server`、`SSH2_MSG_`、`SSH_MSG_`、`ssh_receive` |
| `http` | 80 | `HTTP-fast-server`、`HTTP-url`、`register-protocol \`http'`、`HTTP/1.1`、`HTTP/1.0` |
| `https` | 443 | `HTTPSSL-Server`、`secure-http`、`SSLv3`、`TLS` |
| `telnet` | 23 | `TELNET-Server`、`Telnet`、`VTY` |
| `snmp` | 161 | `SNMP-Server`、`snmpd`、`SNMP` |
| `ipsec` | 500 | `isakmp`、`ISAKMP`、`IKE`、`ipsec` |
| `ftp` | 21 | `FTP-Server`、`FTP-Client` |

marker 选择原则：

- **优先选协议特有的长字符串**，例如 `SSH Process`、`tcp_listen_ssh_port`、
  `SSH2_MSG_KEXINIT`；越短越容易跨组件误命中。
- 进程名/监听名/报文名/CLI 名都可以作为 marker；同一个协议多给几个，
  便于覆盖不同代码区域。
- 如果 marker 在 `.data` 的 Tcl 表里出现但代码里没有 xref，换用协议报文
  字符串（如 `HTTP/1.1`）或 CLI 字符串。

---

## 5. 工作原理

```text
内层 ELF
  │
  ├─ 1. 解析 .text / .rodata / .data 的 offset、VA、size
  │
  ├─ 2. 在 .rodata/.data 里找含 marker 的字符串
  │     为每个字符串登记 linked 和 runtime 两个别名
  │
  ├─ 3. 扫 .text：LUI + ORI/ADDIU/DADDIU 立即数对
  │     命中别名 → 记录 (指令 VA, 字符串)
  │
  ├─ 4. 建函数图：
  │     起点 = 栈序言(addiu/daddiu sp,sp,-N)
  │           ∪ JAL 目标
  │           ∪ 落在 .text 的代码指针常量
  │     每个起点到下一个起点为函数体
  │
  ├─ 5. 把 xref 按函数分组 → 聚类
  │     detail_gap=0x400  → clusters[]
  │     merge_gap=0x8000  → marker_span
  │
  ├─ 6. 入口候选：marker xref 附近 ±8 条指令内
  │     加载到 .text 的 LUI 代码指针
  │     （典型 process_create(name, entry, stack, prio)）
  │
  ├─ 7. 端口常量：全 .text 找 addiu reg,zero,port
  │     标出落在 marker_span 内的引用
  │
  ├─ 8. 边界：marker_span 前后 0x4000 范围内
  │     扫所有 .rodata 字符串 → 每个函数引用的字符串
  │     用来识别“前一个/后一个组件”
  │
  └─ 9. 输出文本摘要 + JSON
```

### 5.1 入口候选是怎么来的

工具不是“知道”哪个函数是入口，而是找下面这种模式：

```asm
lui   $a0, 0x2384
addiu $a0, $a0, -0x6f68     # a0 = 0x23839098 (代码指针)
lui   $a1, 0x26b6
addiu $a1, $a1, -0x2d50     # a1 = 0x26b5d2b0 ("SSH Process")
...
jal   process_create_like
```

于是输出：

```text
entry? : marker='SSH Process' -> code 0x83839098 (rt 0x23839098, phys 0x03839098) @0x838355cc
```

这只是**候选**。需要人工确认：

- 代码指针确实落在 `.text`；
- 它是作为 `process_create` 的 entry 参数、listener 回调、协议结构
  函数指针等被使用；
- 反汇编上下文与角色相符（进程创建/监听注册/报文解析）。

SSH 的四个候选都是真入口或真注册点：

| marker | 候选代码指针 | 角色 |
|---|---:|---|
| `SSH Process` | `0x83839098` | SSH 进程入口 |
| `SSH Event handler` | `0x83848970` | 事件处理器入口 |
| `tcp_listen_ssh_port` | `0x83837250` | TCP 监听回调 |
| `SSH-Server` | `0x8383617c` | SSH 协议结构 getter |

### 5.2 `marker_span` 不是模块范围

`marker_span` 只是“所有 marker 命中都落在这段地址里”。它通常会漏掉：

- 组件开头没有 marker 的客户端/SCP 代码；
- 组件结尾没有 marker 的共享 helper；
- 与协议相关但字符串在 `.data` 或通过指针表引用的代码。

真正的模块范围要看 `boundary_before` / `boundary_after` 里前后函数
引用的字符串，人工扩展。SSH 的例子：

```text
marker_span  M9 0x8382f720 – 0x83848364
boundary_before: scp -v -t %s / SCP Read Process ...   → SSH/SCP 代码
boundary_before: EEM ED RPC (0x8382b7b4-0x8382b834)    → 前一个组件
boundary_after : no host key / SSH memory ...          → SSH helper
boundary_after : CNS SSL Transport (0x8384939c...)      → 后一个组件
最终模块范围 M9 0x8382b834 – 0x8384939c
```

---

## 6. 输出解读

### 6.1 文本输出（SSH M9 示例）

```text
image      : /tmp/.../C2900-UN.BIN
sha256     : 2e045a88...
.text      : 0x81000000 + 0x4956000
delta      : 0x60000000
strings    : 39 matched, 39 code xrefs
clusters   : 8 (detail gap 0x400)

--- cluster 2 ---
  linked : 0x83835590 .. 0x8383564c
  runtime: 0x23835590 .. 0x2383564c
  phys   : 0x03835590 .. 0x0383564c
  funcs  : 1
  entry? : marker='SSH Process' -> code 0x83839098 (rt 0x23839098, phys 0x03839098) @0x838355cc

--- marker span (merge gap 0x8000) ---
  linked : 0x8382f720 .. 0x83848364  (290 funcs)
  runtime: 0x2382f720 .. 0x23848364
  phys   : 0x0382f720 .. 0x03848364
  boundary before (nearest functions):
     0x8382eec8-0x8382f2bc  ['SCP Read Process', 'SCP Write Process']
  boundary after (nearest functions):
     0x8384939c-0x838493d8  ['CNS SSL Transport: ']

port 22: 3801 refs total, 18 inside marker span
  0x838372ac in func 0x83837250-0x838374c0
  0x83837f94 in func 0x83837c90-0x838380b8
  0x83837fc4 in func 0x83837c90-0x838380b8
```

要点：

- `clusters[]` 是明细簇，适合看 marker 分布；
- `marker_span` 是合并跨度，适合做**起点**，不是最终范围；
- `boundary_before/after` 是模块边界的直接证据；
- `port refs` 里可能有很多 22（协议常量/其他用途），要看**函数上下文**
  确认哪个是监听注册；SSH 真正的监听在 `0x83837c90` 里调用
  `tcp_listen_ssh_port(22)`。
- 端口常量总数很大（M9 3801 个 22），不要只看数量。

### 6.2 JSON 结构

```json
{
  "image": {
    "path": "...",
    "sha256": "...",
    "elf_entry": 2164260864,
    "text": {"va": 2164260864, "size": 76898304, "formatted": "0x81000000+0x4956000"},
    "rodata": {"va": 2241159168, "size": 32313376}
  },
  "delta": 1610612736,
  "markers": ["SSH Process", "..."],
  "strings_matched": 39,
  "xrefs": 39,
  "unresolved_xrefs": [],
  "clusters": [
    {
      "start": {"linked": ..., "runtime": ..., "phys": ..., "formatted": {...}},
      "end_exclusive": {"...": "..."},
      "functions": [
        {"start": ..., "end": ..., "formatted": "0x83835590-0x8383564c",
         "markers": ["SSH Process"]}
      ],
      "entry_candidates": [
        {"marker": "SSH Process", "marker_string_va": ...,
         "marker_xref_va": ..., "code_ptr": {"linked": ..., "runtime": ..., "phys": ...},
         "code_ptr_form": "runtime", "code_ptr_load_va": ...}
      ],
      "context": [...]
    }
  ],
  "marker_span": {
    "start": {...},
    "end_exclusive": {...},
    "functions_count": 290,
    "entry_candidates": [...],
    "boundary_before": [{"formatted": "0x8382eec8-0x8382f2bc", "strings": ["SCP Read Process", "..."]}],
    "boundary_after": [{"formatted": "0x8384939c-0x838493d8", "strings": ["CNS SSL Transport: "]}]
  },
  "port": 22,
  "port_refs": [
    {"insn_va": 2164312232, "func_start": 2164312056, "func_end": 2164312444}
  ]
}
```

字段说明：

| 字段 | 含义 |
|---|---|
| `image.sha256` | 内层镜像 SHA256，便于确认分析的是哪份固件 |
| `strings_matched` / `xrefs` | 命中 marker 的字符串数 / 代码 xref 数；xrefs 为 0 见 §10 |
| `clusters[]` | 明细簇；每个簇含函数列表、入口候选、上下文 |
| `marker_span` | 所有 marker 命中合并后的跨度；**不是最终模块范围** |
| `marker_span.entry_candidates` | marker 附近加载的代码指针（入口候选） |
| `marker_span.boundary_before/after` | 前后函数引用的字符串，用于判断模块边界 |
| `port_refs` | 端口常量 `addiu reg,zero,port` 的引用；需结合函数上下文筛选 |

数值字段是十进制/整数；地址在 `formatted` 子字段里是十六进制字符串。
工具输出的所有地址都是 **linked VA**，`runtime`/`phys` 是同一地址的
另外两种表示。

---

## 7. 从输出到 `config/fuzz.json`

### 7.1 覆盖率范围

1. 先看 `marker_span` 和 `boundary_before/after`；
2. 向前扩展到“前一个组件的最后一个函数”之后；
3. 向后扩展到“后一个组件的第一个函数”之前；
4. 得到 linked `[start, end)`，再换算 phys：

```text
coverage_start = linked_start - 0x80000000
coverage_end   = linked_end   - 0x80000000
```

SSH 的最终结果：

| 范围 | linked | phys |
|---|---|---|
| M9 完整 SSH/SCP | `0x8382b834 – 0x8384939c` | `0x0382b834 – 0x0384939c` |
| M7 完整 SSH/SCP | `0x83826fd4 – 0x83844b70` | `0x03826fd4 – 0x03844b70` |
| M9 SSH2 核心 | `0x83839b28 – 0x83844a50` | `0x03839b28 – 0x03844a50` |
| M7 SSH2 核心 | `0x838352c8 – 0x83840224` | `0x038352c8 – 0x03840224` |

完整范围包含 SSH 客户端/SCP 代码；服务端 fuzz 不会执行其中一部分，
但不影响 AFLNet 的覆盖率信号（未执行就不记录）。做“百分比分母”时
再另行标定可达块。

### 7.2 `service_entry`

工具只能给入口候选，不能替你决定字段语义。SSH 建议：

| 语义 | M9 linked | M7 linked |
|---|---:|---:|
| 报文处理入口（推荐填 `service_entry`） | `0x8383e3d8` | `0x838398a8` |
| 服务进程入口 | `0x83839098` | `0x83834838` |
| 监听注册入口 | `0x838374c0` | `0x83832c60` |

> 当前 `service_entry` 在 QEMU 里只解析、未使用（`coverage.c:425`），
> 填它不会改变行为；按 `firmware_entry` 的习惯填 linked VA。

### 7.3 `snapshot_trigger`

找“监听注册调用之后的返回地址”：

- M9：`jal tcp_listen_ssh_port(22)` 在 `0x83837fc0`，返回地址
  `0x83837fc8` → runtime `0x23837fc8`
- M7：对应 `0x83833760` 的返回地址 `0x83833768` → runtime `0x23833768`

`snapshot_trigger` 只在 `auto_snapshot=true` 时生效。SSH 在启动配置里
就已启用，自动触发可能在配置脚本跑完之前就建立快照；项目当前仍建议
`auto_snapshot=false` + 配置完成后手动 `0x01`。

### 7.4 其他字段

| 字段 | 值/来源 |
|---|---|
| `addr_mode` | `phys`（这样 `coverage_start/end` 直接用物理地址） |
| `text_start/text_size` | 从 ELF `.text` 读；M9 `0x81000000/0x4956000`，M7 `0x81000000/0x4950000` |
| `runtime_delta` | `0x60000000` |
| `firmware_entry` | `0x81000000` |
| `snapshot_name` | 按协议命名，如 `snap_ssh` |
| `crash_mode` / `panic_addrs` / `crash_exceptions` | 静态分析一般给不出真实崩溃地址；先 `off`/`log`，用已知崩溃样本标定 |

---

## 8. 新增一个协议的推荐流程

以 HTTP 为例：

### 8.1 拿端口/handler 名

```bash
python3 fw_probe/fw_probe.py --image <inner> --list-services | grep -Ei "http|secure-http"
```

预期看到：

```text
0x882460dc  (register-protocol `http'
0x88246b57  (tcp-well-known-port 80 HTTP-fast-server HTTP-fast-client)
```

### 8.2 跑 preset / marker

```bash
python3 fw_probe/fw_probe.py --image <inner> --preset http \
  --json /tmp/http.json
```

如果 preset 命中的簇太多/太少，调整 marker：

- 太多：换更长的协议字符串，提高 `--detail-gap` 或降低 `--merge-gap`；
- 太少：增加协议报文/CLI/进程名 marker，或检查 runtime alias；
- 0 xrefs：看 §10。

### 8.3 人工确认入口链

按 §5.1 的模式找：

- `process_create(name, entry, ...)` → 服务进程入口；
- listener 注册函数里的端口常量 + 回调函数指针 → TCP/UDP 入口；
- 全局协议结构里的多个 `.text` 函数指针 → accept/read/write/close；
- message code switch / length/MAC 校验字符串 → parser/dispatcher。

### 8.4 用边界字符串确定范围

看 `marker_span.boundary_before/after`：

- 前一个组件的字符串（如 `EEM ED RPC`）；
- 后一个组件的字符串（如 `CNS SSL Transport:`）；
- 扩展 marker_span 到这两个边界之间，换算 phys。

### 8.5 写入协议目录

建议把结果同时落到：

```text
runtime/protocols/<proto>/config/firmware-analysis.json   # 机器可读
runtime/protocols/<proto>/config/fuzz.json                # 或作用域副本
runtime/protocols/<proto>/analysis/                       # probe JSON/反汇编证据
```

不要直接把 M9 地址复制给 M7；两份构建地址不同。

---

## 9. 已分析的 SSH 结果（速查）

详细结果见 `../runtime/protocols/ssh/config/firmware-analysis.json` 与
`（内部笔记，未公开）`。

### 9.1 服务身份

- TCP 22（固定；该镜像无 `ip ssh port` 命令字符串）
- handler `SSH-Server`，NBAR protocol-id 40
- 版本串格式 `SSH-<major>.<minor>-Cisco-1.25`
- 算法：
  - KEX：`diffie-hellman-group-exchange-sha1`、
    `diffie-hellman-group14-sha1`、`diffie-hellman-group1-sha1`
  - HostKey：`ssh-rsa`
  - Cipher：`aes128-cbc`、`3des-cbc`、`aes192-cbc`、`aes256-cbc`
  - MAC：`hmac-sha1`、`hmac-sha1-96`、`hmac-md5`、`hmac-md5-96`

### 9.2 入口（M9 linked）

| 角色 | 地址 |
|---|---:|
| 监听启动/协议 handler 注册 | `0x838374c0` |
| TCP 监听回调 | `0x83837250` |
| accept/会话建立 | `0x83837114` |
| SSHv1 数据入口 | `0x838363a0` |
| `ssh_receive` | `0x83830e5c` |
| SSH2 报文循环（service_entry） | `0x8383e3d8` |
| SSH2 packet parser | `0x8383df88` |
| SSH2 消息分发 | `0x8383e7f8` |
| SSH 进程入口 | `0x83839098` |
| 事件处理器入口 | `0x83848970` |
| 版本串构造 | `0x83833638` |
| 监听就绪返回地址（runtime） | `0x23837fc8` |

### 9.3 范围

见 §7.1 表；项目内已生成：

```text
runtime/protocols/ssh/config/fuzz.ssh-scoped-m9.json
runtime/protocols/ssh/config/fuzz.ssh-scoped-m7.json
runtime/protocols/ssh/config/firmware-analysis.json
```

### 9.4 种子

AFLNet `-P SSH` 的解析器只认**明文**握手（版本行 + 4 字节长度 +
padding + message code）。NEWKEYS 之后加密，AFLNet 无法再解析服务端
响应。项目提供：

```bash
python3 scripts/gen_ssh_seed.py \
  --out runtime/protocols/ssh/seeds/ssh_kexinit.raw
```

生成 `SSH-2.0-OpenSSH_9.0\r\n` + `SSH_MSG_KEXINIT`，算法与服务端列表
有交集。认证/通道状态仍需真实会话抓包种子。

---

## 10. 常见问题与排错

### 10.1 `xrefs: 0`（字符串找到了，但没有代码引用）

按可能性排查：

1. **地址别名不对**：确认 `--delta` 是 `0x60000000`。工具默认会同时搜
   linked 和 runtime；如果目标平台 delta 不同，用 `--delta` 指定。
2. **marker 只出现在 `.data` 的 Tcl 表里**：这张表可能不被代码直接引用，
   换用协议报文/CLI/进程名 marker。
3. **GP 相对寻址**：`ld $a0, offset($gp)` 不会被 LUI 扫描捕获；需要人工
   反汇编或用 capstone/Ghidra。
4. **sreloc 指针表**：部分指针在文件里是 0，启动时才重定位；静态搜不到。
5. **字符串被拆分/拼接**：marker 不是完整字符串，或格式串由多段拼接。

### 10.2 簇太多 / 范围太散

- 提高 `--detail-gap`：把相邻函数合并（默认 `0x400`）；
- 降低 `--merge-gap`：避免把不同组件合在一起（默认 `0x8000`）；
- 换更具体的 marker：`SSH` 会命中 GSI/SSL 等；用 `SSH Process`、
  `tcp_listen_ssh_port`、`SSH2_MSG_KEXINIT`；
- 看 `clusters[]` 里每个簇的 marker 列表，手动排除明显无关的簇。

### 10.3 边界看起来不对

- `marker_span` 本来就不等于模块范围，必须看 `boundary_before/after`；
- 组件可能不连续：IOS 链接顺序不保证所有协议函数相邻；
- 用 `--show-context` 或 JSON 的 `context` 看前后函数的字符串；
- 人工反汇编确认边界函数是否属于目标组件（看它调用的 API/字符串）。

### 10.4 运行很慢

- M9 `.text` 约 77 MB，完整扫描/建函数图约 40 秒，正常；
- 只想拿端口/handler：用 `--list-services`（约 1 秒）；
- 缩小 marker 数量不会显著变快（主要耗时在全 `.text` 扫描）；
- 不要在高频循环里反复调用；每个镜像跑一次，结果存 JSON。

### 10.5 `ModuleNotFoundError: elftools`

```bash
python3 -m pip install pyelftools
```

### 10.6 外层 `.bin` 报“缺少 .text 段”

工具要的是内层 IOS ELF（`C2900-UN.BIN`），不是外层
`c2900-universalk9-mz.SPA.150-1.M9.bin`。按 §1.2 先解 ZIP。

### 10.7 M9 的 ELF machine 是 0xC2

工具只读 ELF 段表/内容，不关心 `e_machine`；QEMU 启动时才需要把
`e_machine` 补成 MIPS（脚本会自动处理）。分析 M9 直接用原始内层镜像
即可。

### 10.8 入口候选是假的

工具用“marker 附近 ±8 条指令内加载的 `.text` 指针”做启发式，可能把
数据指针/无关函数当入口。确认方法：

- 反汇编候选附近，看调用点参数顺序是否符合 `process_create`/
  listener/回调注册；
- 看候选函数开头是否有栈序言、是否被 `jal` 调用；
- 看候选是否在 `marker_span`/模块范围内；
- 与 `runtime/protocols/ssh/analysis/m9-key-functions.txt` 这类人工证据对照。

---

## 11. 限制与正确性边界

1. **纯静态**：不启动 QEMU、不发包；所有范围/入口是静态候选，最终要用
   真实种子跑一轮验证 `ios_tb_out_range` 和覆盖率增长。
2. **只识别 LUI+ORI/ADDIU/DADDIU**：GP 相对寻址、sreloc 指针表、
   运行时生成的跳转表可能漏。
3. **函数边界是启发式**：栈序言 + JAL 目标 + 代码指针常量；不是严格
   符号表，可能过分割/漏掉叶子函数。
4. **间接调用**：`jalr`/函数指针表只能靠附近加载的代码指针猜，可能漏
   或多报。
5. **共享代码不在范围**：RSA/DH/AES、AAA、TCP/IP 栈是共享库；协议
   range 只覆盖协议组件本身。
6. **NBAR 表不是服务启动表**：`.data` 的 Tcl 协议定义用于分类/识别；
   端口/handler 名可作线索，服务启动代码仍要用 marker 找。
7. **地址不能跨镜像混用**：M7/M9/其他版本布局不同；每次换镜像都要
   重新跑工具。
8. **`service_entry` 未接线**：QEMU 当前只解析不使用；不要指望填了就
   自动改变覆盖率过滤。
9. **端口常量有噪声**：`addiu reg,zero,22` 可能出现在很多无关函数；
   结合函数上下文/监听 API 确认。
10. **工具不判断漏洞**：它只定位代码/入口/范围，不做漏洞分析。

---

## 12. 相关文件

| 文件 | 内容 |
|---|---|
| `../runtime/protocols/ssh/config/firmware-analysis.json` | SSH 入口/范围/config/方法（机器可读） |
| `（内部笔记，未公开）` | SSH 逆向摘要（详细报告随 docs 备份） |
| `（内部笔记，未公开）` | 内层镜像提取、段布局、资源树 |
| `../runtime/protocols/ssh/analysis/m9-ssh.json` | M9 probe 原始输出 |
| `../runtime/protocols/ssh/analysis/m7-ssh.json` | M7 probe 原始输出 |
| `../runtime/protocols/ssh/analysis/m9-key-functions.txt` | M9 关键函数反汇编 |
| `../runtime/protocols/ssh/analysis/m7-key-functions.txt` | M7 关键函数反汇编 |
| `../runtime/protocols/ssh/analysis/ssh-strings.txt` | SSH 字符串/算法/三种地址 |
| `../runtime/protocols/ssh/analysis/service-registry.txt` | `.data` 内部协议定义表 |
| `../runtime/protocols/ssh/config/firmware-analysis.json` | SSH 机器可读汇总 |
| `../scripts/gen_ssh_seed.py` | SSH 明文握手种子生成器（配套） |
| `（内部笔记，未公开）` | 交接用快速上手指南（工具/方法/SSH 样例/清单） |

---

## 13. 变更记录

- 2026-10-26：首个版本。从 `scripts/fw_probe.py` 独立到 `fw_probe/`，
  补充本 README；SSH M9/M7 分析结果见 §9。
