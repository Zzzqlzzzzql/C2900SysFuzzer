#!/usr/bin/env python3
"""fw_probe.py — 在 C2900 IOS 内层镜像中定位协议子系统的入口与覆盖率范围。

不启动 QEMU，只对固件 ELF 做静态分析。核心事实（已由实测日志确认）：

  * 代码链接地址是 0x81000000（.text），但运行时以“运行时别名”执行：
        runtime = linked - 0x60000000
    例如 linked 0x8111c798 运行时是 0x2111c798，物理地址是 0x0111c798。
  * 因此代码引用 .rodata 字符串时，立即数里出现的是字符串 VA 减
    0x60000000，而不是 ELF 里的链接 VA。直接搜链接 VA 会 0 命中。
  * 物理地址（fuzz.json 的 coverage_start/end 使用）：
        phys = linked - 0x80000000   (等价于 linked & 0x1fffffff)

用法示例（SSH，M9）：

  # 1) 先看 .data 内部协议定义表（NBAR 风格；端口/handler 名，最快）
  python3 fw_probe/fw_probe.py --image <inner> --list-services

  # 2) 用 preset（或显式 --marker/--port）找代码入口/范围
  python3 fw_probe/fw_probe.py \
    --image /tmp/binwalk_m9/_c2900-universalk9-mz.SPA.150-1.M9.bin.extracted/C2900-UN.BIN \
    --preset ssh --json runtime/protocols/ssh/analysis/m9-ssh.json

  # 等价显式写法
  python3 fw_probe/fw_probe.py \
    --image <inner> \
    --marker "SSH Process" --marker "SSH Event handler" \
    --marker "tcp_listen_ssh_port" --marker "SSH-Server" \
    --marker "SSH2_MSG_" --marker "SSH_MSG_" --marker "ssh_receive" \
    --port 22 --json out.json

preset 支持：ssh/http/https/telnet/snmp/ipsec/ftp（只是起点，按版本调整）。

输出：
  * 每个 marker 字符串的 VA 与代码 xref；
  * marker 命中的函数簇（linked / runtime / phys 三种地址）；
  * 入口候选：与 marker 字符串相邻、被当作参数传递的代码指针
    （典型是 process_create("SSH Process", entry, ...) 和
     process_create("SSH Event handler", entry, ...)）；
  * 端口常量引用（如 addiu reg,zero,22）；
  * 簇的前后边界函数，便于人工确认模块起止。

限制：
  * 只识别 LUI + ORI/ADDIU/DADDIU 构造的地址；GP 相对寻址、纯函数指针表
    （由 sreloc 在启动时重定位）可能漏掉。漏掉时用 --marker 选更具体的
    字符串，或人工反汇编确认。
  * 函数边界由“序言 + JAL 目标 + 代码指针常量”推导，用于聚类足够，
    不是严格的符号表。
"""

import argparse
import collections
import hashlib
import json
import re
import struct
import sys

try:
    from elftools.elf.elffile import ELFFile
except ImportError:
    sys.exit("需要 pyelftools: python3 -m pip install pyelftools")

RUNTIME_DELTA_DEFAULT = 0x60000000
KSEG0_BASE = 0x80000000

# 常用协议的一手 marker/端口。preset 只是起点：不同 IOS 版本/组件名可能不同，
# 先在 .data 内部协议定义表里确认 handler 名，再按需增删 marker。
PRESETS = {
    "ssh": {
        "port": 22,
        "markers": [
            "SSH Process", "SSH Event handler", "tcp_listen_ssh_port",
            "SSH-Server", "SSH2_MSG_", "SSH_MSG_", "ssh_receive",
        ],
    },
    "http": {
        "port": 80,
        "markers": [
            "HTTP-fast-server", "HTTP-url", "register-protocol `http'",
            "HTTP/1.1", "HTTP/1.0",
        ],
    },
    "https": {
        "port": 443,
        "markers": ["HTTPSSL-Server", "secure-http", "SSLv3", "TLS"],
    },
    "telnet": {
        "port": 23,
        "markers": ["TELNET-Server", "Telnet", "VTY"],
    },
    "snmp": {
        "port": 161,
        "markers": ["SNMP-Server", "snmpd", "SNMP"],
    },
    "ipsec": {
        "port": 500,
        "markers": ["isakmp", "ISAKMP", "IKE", "ipsec"],
    },
    "ftp": {
        "port": 21,
        "markers": ["FTP-Server", "FTP-Client"],
    },
}


def sign16(v):
    return v - 0x10000 if v & 0x8000 else v


def sign32(v):
    return v - 0x100000000 if v & 0x80000000 else v


class Image:
    def __init__(self, path):
        self.path = path
        self.fh = open(path, "rb")
        self.elf = ELFFile(self.fh)
        self.sections = {}
        for sec in self.elf.iter_sections():
            if sec["sh_type"] == "SHT_NOBITS" or sec["sh_size"] == 0:
                continue
            self.sections[sec.name] = (
                sec["sh_offset"],
                sec["sh_addr"],
                sec["sh_size"],
            )
        for needed in (".text", ".rodata"):
            if needed not in self.sections:
                raise SystemExit(f"{path}: 缺少 {needed} 段")
        self.text_off, self.text_va, self.text_sz = self.sections[".text"]
        self.text = self._read(self.text_off, self.text_sz)
        self.nwords = len(self.text) // 4
        self.words = struct.unpack(">%dI" % self.nwords, self.text[: self.nwords * 4])
        self.sha256 = self._sha256()

    def _read(self, off, size):
        self.fh.seek(off)
        return self.fh.read(size)

    def _sha256(self):
        h = hashlib.sha256()
        with open(self.path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    def va_to_off(self, va):
        for off, addr, size in self.sections.values():
            if addr <= va < addr + size:
                return off + (va - addr)
        return None

    def read_cstr(self, va, limit=256):
        off = self.va_to_off(va)
        if off is None:
            return None
        self.fh.seek(off)
        raw = self.fh.read(limit)
        end = raw.find(b"\0")
        if end >= 0:
            raw = raw[:end]
        try:
            return raw.decode("latin1")
        except UnicodeDecodeError:
            return None

    def collect_strings(self, markers, ignore_case=False):
        """返回 [{va, section, string}]，只看 .rodata/.data。"""
        out = []
        pat = re.compile(rb"[ -~]{4,}")
        for name in (".rodata", ".data"):
            if name not in self.sections:
                continue
            off, addr, size = self.sections[name]
            data = self._read(off, size)
            for m in pat.finditer(data):
                s = m.group().decode("latin1")
                hay = s.lower() if ignore_case else s
                if any((mk.lower() if ignore_case else mk) in hay for mk in markers):
                    out.append({"va": addr + m.start(), "section": name, "string": s})
        return out

    def build_function_map(self):
        """函数起点 = 栈序言 ∪ JAL 目标 ∪ 落在 .text 的代码指针常量。"""
        words = self.words
        starts = set()
        for i, w in enumerate(words):
            op = w >> 26
            rs = (w >> 21) & 0x1F
            rt = (w >> 16) & 0x1F
            imm = w & 0xFFFF
            if op in (0x09, 0x19) and rs == 29 and rt == 29 and (imm & 0x8000):
                starts.add(i)
        for i, w in enumerate(words):
            if (w >> 26) == 0x03:  # JAL
                tgt = (self.text_va & 0xF0000000) | ((w & 0x3FFFFFF) << 2)
                if self.text_va <= tgt < self.text_va + self.text_sz:
                    starts.add((tgt - self.text_va) // 4)
        # 代码指针常量
        pend = collections.defaultdict(list)
        for i, w in enumerate(words):
            op = w >> 26
            rs = (w >> 21) & 0x1F
            rt = (w >> 16) & 0x1F
            imm = w & 0xFFFF
            for r in list(pend):
                pend[r] = [p for p in pend[r] if i - p[0] <= 6]
                if not pend[r]:
                    del pend[r]
            if op == 0x0F:
                pend[rt].append((i, imm << 16))
                continue
            if op in (0x0D, 0x09, 0x19) and rs in pend:
                for _, hi in pend[rs]:
                    if op == 0x0D:
                        low = (hi | imm) & 0xFFFFFFFF
                    else:
                        low = (
                            (hi | (0xFFFFFFFF00000000 if hi & 0x80000000 else 0))
                            + sign16(imm)
                        ) & 0xFFFFFFFF
                    for cand in (low, (low + RUNTIME_DELTA_DEFAULT) & 0xFFFFFFFF):
                        if self.text_va <= cand < self.text_va + self.text_sz and cand % 4 == 0:
                            starts.add((cand - self.text_va) // 4)
        ordered = sorted(starts)
        funcs = []
        for idx, s in enumerate(ordered):
            e = ordered[idx + 1] if idx + 1 < len(ordered) else self.nwords
            funcs.append((s, e))
        return funcs

    def func_at(self, va):
        i = (va - self.text_va) // 4
        lo, hi = 0, len(self.funcs) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            s, e = self.funcs[mid]
            if i < s:
                hi = mid - 1
            elif i >= e:
                lo = mid + 1
            else:
                return self.funcs[mid]
        return None

    def va_of_word(self, i):
        return self.text_va + i * 4


def find_xrefs(img, string_entries, delta, window=8):
    """找 LUI + ORI/ADDIU/DADDIU 构造出的字符串地址。

    对每个字符串 VA 同时登记 linked / runtime 两个别名；命中后记录
    (指令 VA, 目标字符串, 构造方式)。
    """
    alias = {}
    for ent in string_entries:
        va = ent["va"]
        for form, val in (
            ("linked", va),
            ("runtime", (va - delta) & 0xFFFFFFFF),
        ):
            alias.setdefault(val, []).append((ent, form))
    words = img.words
    pend = collections.defaultdict(list)
    hits = []
    for i, w in enumerate(words):
        op = w >> 26
        rs = (w >> 21) & 0x1F
        rt = (w >> 16) & 0x1F
        imm = w & 0xFFFF
        for r in list(pend):
            pend[r] = [p for p in pend[r] if i - p[0] <= window]
            if not pend[r]:
                del pend[r]
        if op == 0x0F:
            pend[rt].append((i, imm << 16))
            continue
        if op not in (0x0D, 0x09, 0x19) or rs not in pend:
            continue
        for lui_idx, hi in pend[rs]:
            if op == 0x0D:
                low = (hi | imm) & 0xFFFFFFFF
                kind = "lui+ori"
            else:
                low = (
                    (hi | (0xFFFFFFFF00000000 if hi & 0x80000000 else 0))
                    + sign16(imm)
                ) & 0xFFFFFFFF
                kind = "lui+addiu" if op == 0x09 else "lui+daddiu"
            for ent, form in alias.get(low, ()):
                hits.append(
                    {
                        "insn_va": img.va_of_word(i),
                        "lui_va": img.va_of_word(lui_idx),
                        "kind": kind,
                        "form": form,
                        "string_va": ent["va"],
                        "string": ent["string"],
                        "section": ent["section"],
                    }
                )
    return hits


def code_pointer_near(img, insn_idx, delta, radius=6):
    """在 insn 前后 radius 条指令内找落在 .text 的 LUI 立即数。

    返回 [(code_va_linked, raw_value, at_va, form)]。这是识别
    process_create(name, entry, ...) / 回调注册的启发式。
    """
    lo = max(0, insn_idx - radius)
    hi = min(img.nwords, insn_idx + radius + 1)
    pend = collections.defaultdict(list)
    out = []
    for i in range(lo, hi):
        w = img.words[i]
        op = w >> 26
        rs = (w >> 21) & 0x1F
        rt = (w >> 16) & 0x1F
        imm = w & 0xFFFF
        if op == 0x0F:
            pend[rt].append((i, imm << 16))
            continue
        if op not in (0x0D, 0x09, 0x19) or rs not in pend:
            continue
        for _, hi16 in pend[rs]:
            if op == 0x0D:
                low = (hi16 | imm) & 0xFFFFFFFF
            else:
                low = (
                    (hi16 | (0xFFFFFFFF00000000 if hi16 & 0x80000000 else 0))
                    + sign16(imm)
                ) & 0xFFFFFFFF
            for form, cand in (("runtime", low), ("linked", (low + delta) & 0xFFFFFFFF)):
                if img.text_va <= cand < img.text_va + img.text_sz and cand % 4 == 0:
                    out.append((cand, low, img.va_of_word(i), form))
    return out


def port_refs(img, port):
    """在 .text 全范围找 addiu reg,zero,port（端口常量）。"""
    out = []
    for i, w in enumerate(img.words):
        op = w >> 26
        rs = (w >> 21) & 0x1F
        rt = (w >> 16) & 0x1F
        imm = w & 0xFFFF
        if op == 0x09 and rs == 0 and imm == port:
            va = img.va_of_word(i)
            f = img.func_at(va)
            out.append(
                {
                    "insn_va": va,
                    "func_start": img.va_of_word(f[0]) if f else None,
                    "func_end": img.va_of_word(f[1]) if f else None,
                }
            )
    return out


def merge_clusters(functions, gap):
    """把 marker 命中的函数按地址合并成簇；gap 内的间隔视为同一簇。"""
    if not functions:
        return []
    functions = sorted(functions)
    clusters = []
    cur = list(functions[0])
    for s, e in functions[1:]:
        if s <= cur[1] + gap:
            cur[1] = max(cur[1], e)
        else:
            clusters.append(tuple(cur))
            cur = [s, e]
    clusters.append(tuple(cur))
    return clusters


def context_string_map(img, lo_va, hi_va):
    """在 [lo_va, hi_va) 内扫描指令，返回函数 -> 引用字符串列表。

    用于人工判断模块边界（例如 SSH 前面是 EEM ED RPC，后面是
    CNS SSL Transport）。只扫窗口内的指令，代价很低。
    """
    o, a, sz = img.sections[".rodata"]
    data = img._read(o, sz)
    pat = re.compile(rb"[ -~]{5,}")
    alias = {}
    for m in pat.finditer(data):
        va = a + m.start()
        s = m.group().decode("latin1")
        alias[va] = s
        alias[(va - RUNTIME_DELTA_DEFAULT) & 0xFFFFFFFF] = s
    pend = collections.defaultdict(list)
    out = collections.defaultdict(list)
    lo_i = max(0, (lo_va - img.text_va) // 4)
    hi_i = min(img.nwords, (hi_va - img.text_va) // 4 + 1)
    for i in range(max(0, lo_i - 8), hi_i):
        w = img.words[i]
        op = w >> 26
        rs = (w >> 21) & 0x1F
        rt = (w >> 16) & 0x1F
        imm = w & 0xFFFF
        for r in list(pend):
            pend[r] = [p for p in pend[r] if i - p[0] <= 8]
            if not pend[r]:
                del pend[r]
        if op == 0x0F:
            pend[rt].append((i, imm << 16))
            continue
        if op not in (0x0D, 0x09, 0x19) or rs not in pend:
            continue
        for _, hi16 in pend[rs]:
            if op == 0x0D:
                low = (hi16 | imm) & 0xFFFFFFFF
            else:
                low = (
                    (hi16 | (0xFFFFFFFF00000000 if hi16 & 0x80000000 else 0))
                    + sign16(imm)
                ) & 0xFFFFFFFF
            if low in alias and lo_i <= i < hi_i:
                f = img.func_at(img.va_of_word(i))
                if f:
                    s = alias[low]
                    if s not in out[f]:
                        out[f].append(s)
    return out


def addr_forms(img, va, delta):
    return {
        "linked": va,
        "runtime": (va - delta) & 0xFFFFFFFF,
        "phys": (va - KSEG0_BASE) & 0xFFFFFFFF,
        "formatted": {
            "linked": f"0x{va:08x}",
            "runtime": f"0x{(va - delta) & 0xFFFFFFFF:08x}",
            "phys": f"0x{(va - KSEG0_BASE) & 0xFFFFFFFF:08x}",
        },
    }


SERVICE_PAT = re.compile(
    r"\((tcp-static-port|udp-static-port|combined-static-port|"
    r"combined-dual-static-port|tcp-well-known-port|udp-well-known-port|"
    r"register-protocol|protocol-id)\b"
)


def list_services(img):
    """列出 .data 里 IOS 内部 Tcl 协议定义表（NBAR 风格）的端口/handler 行。"""
    if ".data" not in img.sections:
        raise SystemExit("镜像没有 .data 段")
    off, addr, size = img.sections[".data"]
    data = img._read(off, size)
    pos = 0
    for raw in data.split(b"\n"):
        line = raw.decode("latin1", "replace").strip()
        if SERVICE_PAT.search(line):
            print(f"0x{addr + pos:08x}  {line}")
        pos += len(raw) + 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, help="内层 IOS ELF（C2900-UN.BIN 等）")
    ap.add_argument("--marker", action="append", help="协议特征字符串，可重复")
    ap.add_argument("--preset", choices=sorted(PRESETS),
                    help="常用协议的 marker/端口预设（可用 --marker/--port 覆盖）")
    ap.add_argument("--list-services", action="store_true",
                    help="只列出 .data 内部协议定义表（端口/handler）并退出")
    ap.add_argument("--ignore-case", action="store_true")
    ap.add_argument("--port", type=lambda s: int(s, 0), default=None, help="协议端口，用于找端口常量")
    ap.add_argument("--delta", type=lambda s: int(s, 0), default=RUNTIME_DELTA_DEFAULT,
                    help="运行时重定位差（默认 0x60000000）")
    ap.add_argument("--merge-gap", type=lambda s: int(s, 0), default=0x8000,
                    help="合并 marker 簇的间隔阈值（默认 0x8000；不同模块请调小）")
    ap.add_argument("--detail-gap", type=lambda s: int(s, 0), default=0x400,
                    help="明细簇的间隔阈值（默认 0x400）")
    ap.add_argument("--json", help="输出 JSON 路径")
    ap.add_argument("--show-context", type=int, default=3, help="每个簇上下各显示几个函数")
    args = ap.parse_args()

    if args.preset:
        preset = PRESETS[args.preset]
        if not args.marker:
            args.marker = list(preset["markers"])
        if args.port is None:
            args.port = preset["port"]
        print(f"preset     : {args.preset} (markers={len(args.marker)}, port={args.port})")

    img = Image(args.image)
    if args.list_services:
        list_services(img)
        return
    if not args.marker:
        ap.error("需要 --marker（或使用 --list-services）")
    img.funcs = img.build_function_map()
    strings = img.collect_strings(args.marker, args.ignore_case)
    if not strings:
        raise SystemExit(f"未找到包含 {args.marker} 的字符串")

    hits = find_xrefs(img, strings, args.delta)

    # 按函数分组
    func_hits = collections.defaultdict(list)
    unresolved = []
    for h in hits:
        f = img.func_at(h["insn_va"])
        if f is None:
            unresolved.append(h)
        else:
            func_hits[f].append(h)

    funcs = sorted(func_hits)
    clusters = merge_clusters(funcs, args.detail_gap)
    merged = merge_clusters(funcs, args.merge_gap)
    marker_span = merged[0] if merged else None
    if marker_span:
        marker_span = list(marker_span)
        for s, e in merged[1:]:
            marker_span[1] = max(marker_span[1], e)
        marker_span = tuple(marker_span)

    report_clusters = []
    for cs, ce in clusters:
        start_va = img.va_of_word(cs)
        end_va = img.va_of_word(ce)
        func_list = []
        entry_candidates = []
        for f in funcs:
            if not (cs <= f[0] < ce):
                continue
            fstart = img.va_of_word(f[0])
            fend = img.va_of_word(f[1])
            marks = sorted({h["string"] for h in func_hits[f]})
            func_list.append(
                {
                    "start": fstart,
                    "end": fend,
                    "formatted": f"0x{fstart:08x}-0x{fend:08x}",
                    "markers": marks,
                }
            )
            for h in func_hits[f]:
                idx = (h["insn_va"] - img.text_va) // 4
                for code_va, _raw, at_va, form in code_pointer_near(
                    img, idx, args.delta, radius=8
                ):
                    entry_candidates.append(
                        {
                            "marker": h["string"],
                            "marker_string_va": h["string_va"],
                            "marker_xref_va": h["insn_va"],
                            "code_ptr": addr_forms(img, code_va, args.delta),
                            "code_ptr_form": form,
                            "code_ptr_load_va": at_va,
                        }
                    )
        # 去重
        seen = set()
        uniq = []
        for c in entry_candidates:
            key = (c["marker"], c["code_ptr"]["linked"])
            if key in seen:
                continue
            seen.add(key)
            uniq.append(c)

        context = []
        for f in img.funcs:
            if ce <= f[0] <= ce + args.show_context * 0x1000 or \
               max(0, cs - args.show_context * 0x1000) <= f[0] < cs:
                context.append(
                    {
                        "start": img.va_of_word(f[0]),
                        "end": img.va_of_word(f[1]),
                        "formatted": f"0x{img.va_of_word(f[0]):08x}-0x{img.va_of_word(f[1]):08x}",
                    }
                )

        report_clusters.append(
            {
                "start": addr_forms(img, start_va, args.delta),
                "end_exclusive": addr_forms(img, end_va, args.delta),
                "functions": func_list,
                "entry_candidates": uniq,
                "context": context[:2 * args.show_context + 2],
            }
        )

    # marker 合并跨度：只说明“所有 marker 命中都落在这段里”，
    # 模块真正边界需要看前后函数引用的字符串人工确认。
    span_info = None
    boundary_before = []
    boundary_after = []
    if marker_span:
        span_start = img.va_of_word(marker_span[0])
        span_end = img.va_of_word(marker_span[1])
        # 所有明细簇的入口候选聚合
        entries = []
        seen = set()
        for c in report_clusters:
            for ec in c["entry_candidates"]:
                key = (ec["marker"], ec["code_ptr"]["linked"])
                if key in seen:
                    continue
                seen.add(key)
                entries.append(ec)
        funcs_in_span = [
            f for f in img.funcs
            if span_start <= img.va_of_word(f[0]) < span_end
        ]
        # 边界上下文：marker 跨度前后各 0x4000 的函数及其字符串
        ctx_before = context_string_map(img, max(img.text_va, span_start - 0x4000), span_start)
        ctx_after = context_string_map(img, span_end, min(img.text_va + img.text_sz, span_end + 0x4000))
        for f, ss in sorted(ctx_before.items()):
            boundary_before.append(
                {
                    "formatted": f"0x{img.va_of_word(f[0]):08x}-0x{img.va_of_word(f[1]):08x}",
                    "strings": ss[:4],
                }
            )
        for f, ss in sorted(ctx_after.items()):
            boundary_after.append(
                {
                    "formatted": f"0x{img.va_of_word(f[0]):08x}-0x{img.va_of_word(f[1]):08x}",
                    "strings": ss[:4],
                }
            )
        span_info = {
            "start": addr_forms(img, span_start, args.delta),
            "end_exclusive": addr_forms(img, span_end, args.delta),
            "functions_count": len(funcs_in_span),
            "entry_candidates": entries,
            "boundary_before": boundary_before[-4:],
            "boundary_after": boundary_after[:4],
        }

    port_hits = []
    if args.port is not None:
        port_hits = port_refs(img, args.port)

    report = {
        "image": {
            "path": args.image,
            "sha256": img.sha256,
            "elf_entry": img.elf["e_entry"],
            "text": {"va": img.text_va, "size": img.text_sz,
                     "formatted": f"0x{img.text_va:08x}+0x{img.text_sz:x}"},
            "rodata": ({"va": img.sections[".rodata"][1], "size": img.sections[".rodata"][2]}
                       if ".rodata" in img.sections else None),
        },
        "delta": args.delta,
        "markers": args.marker,
        "strings_matched": len(strings),
        "xrefs": len(hits),
        "unresolved_xrefs": unresolved,
        "clusters": report_clusters,
        "marker_span": span_info,
        "port": args.port,
        "port_refs": port_hits,
    }

    # 文本摘要
    print(f"image      : {args.image}")
    print(f"sha256     : {img.sha256}")
    print(f".text      : 0x{img.text_va:08x} + 0x{img.text_sz:x}")
    print(f"delta      : 0x{args.delta:08x}")
    print(f"strings    : {len(strings)} matched, {len(hits)} code xrefs")
    print(f"clusters   : {len(report_clusters)} (detail gap 0x{args.detail_gap:x})")
    for ci, c in enumerate(report_clusters):
        print(f"\n--- cluster {ci} ---")
        print(f"  linked : 0x{c['start']['linked']:08x} .. 0x{c['end_exclusive']['linked']:08x}")
        print(f"  runtime: 0x{c['start']['runtime']:08x} .. 0x{c['end_exclusive']['runtime']:08x}")
        print(f"  phys   : 0x{c['start']['phys']:08x} .. 0x{c['end_exclusive']['phys']:08x}")
        print(f"  funcs  : {len(c['functions'])}")
        for ec in c["entry_candidates"]:
            print(
                "  entry? : marker={!r} -> code 0x{:08x} (rt 0x{:08x}, phys 0x{:08x}) @0x{:08x}".format(
                    ec["marker"][:48],
                    ec["code_ptr"]["linked"],
                    ec["code_ptr"]["runtime"],
                    ec["code_ptr"]["phys"],
                    ec["code_ptr_load_va"],
                )
            )
    if span_info:
        st, en = span_info["start"], span_info["end_exclusive"]
        print(f"\n--- marker span (merge gap 0x{args.merge_gap:x}) ---")
        print(f"  linked : 0x{st['linked']:08x} .. 0x{en['linked']:08x}  ({span_info['functions_count']} funcs)")
        print(f"  runtime: 0x{st['runtime']:08x} .. 0x{en['runtime']:08x}")
        print(f"  phys   : 0x{st['phys']:08x} .. 0x{en['phys']:08x}")
        print("  boundary before (nearest functions):")
        for b in span_info["boundary_before"]:
            print(f"     {b['formatted']}  {b['strings']}")
        print("  boundary after (nearest functions):")
        for b in span_info["boundary_after"]:
            print(f"     {b['formatted']}  {b['strings']}")
    if port_hits:
        inside = [p for p in port_hits
                  if span_info and span_info["start"]["linked"] <= p["insn_va"] < span_info["end_exclusive"]["linked"]]
        print(f"\nport {args.port}: {len(port_hits)} refs total, {len(inside)} inside marker span")
        for p in inside[:20]:
            print(f"  0x{p['insn_va']:08x} in func 0x{p['func_start']:08x}-0x{p['func_end']:08x}")
    if span_info:
        print("\nNOTE: marker span 只是 marker 覆盖范围；coverage_start/end 应再")
        print("      向前/向后扩展到模块边界（看 boundary 字符串），人工确认后写入。")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nJSON written: {args.json}")


if __name__ == "__main__":
    main()
