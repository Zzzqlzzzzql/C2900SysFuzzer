#!/usr/bin/env python3
"""C2900 固件内嵌 monlib/FFS 模块的离线分析。

用途：
  1. 从 M7/M9 内层 IOS 镜像中自动定位并提取内嵌 monlib ELF
     （e_machine=0xC4, entry=0x80008000）；
  2. 解析 .text / .rodata / .pdr；.pdr 提供无符号 ELF 的函数边界；
  3. 反汇编 .text，找出引用 .rodata 字符串的函数，用字符串给函数命名线索；
  4. 输出 JSON，供覆盖率日志的地址→函数映射使用。

用法:
  ./monlib_analysis.py --firmware <C2900-UN.BIN> [--json out.json]
"""
import argparse
import bisect
import hashlib
import io
import json
import struct
import sys

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_BIG_ENDIAN
from elftools.elf.elffile import ELFFile


def find_monlib(fw):
    d = open(fw, "rb").read()
    i = 0
    while True:
        j = d.find(b"\x7fELF\x01\x02\x01", i)
        if j < 0:
            break
        try:
            e = struct.unpack(">16sHHIIIIIHHHHHH", d[j:j + 52])
        except struct.error:
            break
        if e[2] == 0xC4 and e[4] == 0x80008000:
            return d, j
        i = j + 1
    raise SystemExit("未在 %s 中找到 monlib ELF" % fw)


def extract_elf(d, off):
    e = struct.unpack(">16sHHIIIIIHHHHHH", d[off:off + 52])
    shoff, shentsize, shnum = e[6], e[11], e[12]
    end = 0
    for i in range(shnum):
        s = struct.unpack(">IIIIIIIIII", d[off + shoff + i * shentsize:
                                            off + shoff + (i + 1) * shentsize])
        if s[1] not in (0, 8):  # NULL / NOBITS
            end = max(end, s[4] + s[5])
    return d[off:off + end]


def elf_sections(blob):
    elf = ELFFile(io.BytesIO(blob))
    secs = {}
    for s in elf.iter_sections():
        secs[s.name] = s
    return elf, secs


def parse_pdr(blob):
    elf, secs = elf_sections(blob)
    text = secs[".text"]
    tstart, tsize = text["sh_addr"], text["sh_size"]
    pdr = secs[".pdr"].data()
    addrs = set()
    for i in range(0, len(pdr), 8):
        a, flags = struct.unpack(">II", pdr[i:i + 8])
        if tstart <= a < tstart + tsize:
            addrs.add(a)
    addrs = sorted(addrs)
    funcs = []
    for i, a in enumerate(addrs):
        end = addrs[i + 1] if i + 1 < len(addrs) else tstart + tsize
        funcs.append({"start": a, "end": end, "size": end - a})
    return {"elf": elf, "secs": secs, "text_start": tstart,
            "text_size": tsize, "funcs": funcs, "addrs": addrs}


def func_at(info, pc):
    i = bisect.bisect_right(info["addrs"], pc) - 1
    if i >= 0 and info["addrs"][i] <= pc < info["funcs"][i]["end"]:
        return i
    return None


def read_strings(sec):
    data = sec.data()
    base = sec["sh_addr"]
    out = {}
    pos = 0
    while pos < len(data):
        end = data.find(b"\0", pos)
        if end < 0:
            break
        s = data[pos:end]
        if len(s) >= 5:
            try:
                text = s.decode("ascii")
            except UnicodeDecodeError:
                text = None
            if text is not None:
                out[base + pos] = text
        pos = end + 1
    return out


def find_refs(info, strings):
    """反汇编 .text，跟踪 lui/addiu/ori 构造的绝对地址，找 .rodata 字符串引用。"""
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_BIG_ENDIAN)
    md.detail = True
    md.skipdata = True  # .text 内混有跳转表/数据，否则线性反汇编会中途停止
    text = info["secs"][".text"].data()
    bases = [s["sh_addr"] for s in (info["secs"][".rodata"], info["secs"][".data"])]
    str_lo = min(strings)
    str_hi = max(strings) + 1
    refs = []
    regs = {}
    for ins in md.disasm(text, info["text_start"]):
        if ins.id == 0:  # SKIPDATA 伪指令
            continue
        try:
            ops = ins.operands
        except Exception:
            continue
        m = ins.mnemonic
        if m == "lui" and len(ops) == 2 and ops[0].type == 1:  # REG
            regs[ins.reg_name(ops[0].reg)] = (ops[1].imm & 0xffff) << 16
        elif m in ("addiu", "ori") and len(ops) == 3 and ops[0].type == 1 \
                and ops[1].type == 1:
            dst = ins.reg_name(ops[0].reg)
            src = ins.reg_name(ops[1].reg)
            if src in regs:
                imm = ops[2].imm
                if m == "addiu":
                    imm = imm - (1 << 32) if imm >= (1 << 31) else imm
                val = (regs[src] + imm) & 0xffffffff
                regs[dst] = val
                if str_lo <= val < str_hi and val in strings:
                    fi = func_at(info, ins.address)
                    refs.append({"pc": ins.address, "target": val,
                                 "string": strings[val], "func_index": fi})
                elif any(abs(val - b) < 0x200000 for b in bases) and val not in strings:
                    pass
            else:
                regs.pop(dst, None)
        else:
            # 其它真正写寄存器的指令：使构造值失效
            try:
                writes = ins.regs_access()[3]
            except Exception:
                writes = []
            for rid in writes:
                regs.pop(md.reg_name(rid), None)
    return refs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--firmware", required=True,
                    help="内层 IOS 镜像（M7 C2900-UN.150.BIN 或 M9 C2900-UN.BIN）")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    fw_bytes, off = find_monlib(args.firmware)
    blob = extract_elf(fw_bytes, off)
    info = parse_pdr(blob)
    strings = read_strings(info["secs"][".rodata"])
    refs = find_refs(info, strings)

    # 每个函数的字符串线索
    by_func = {}
    for r in refs:
        fi = r["func_index"]
        if fi is None:
            continue
        by_func.setdefault(fi, []).append(r["string"])

    out = {
        "firmware": args.firmware,
        "firmware_sha256": hashlib.sha256(fw_bytes).hexdigest(),
        "module_offset": off,
        "module_size": len(blob),
        "module_sha256": hashlib.sha256(blob).hexdigest(),
        "entry": 0x80008000,
        "text_start": info["text_start"],
        "text_size": info["text_size"],
        "rodata_start": info["secs"][".rodata"]["sh_addr"],
        "rodata_size": info["secs"][".rodata"]["sh_size"],
        "pdr_functions": len(info["funcs"]),
        "string_refs": len(refs),
        "functions_with_strings": {},
        "functions": [],
    }
    hints = {}
    for fi in sorted(by_func):
        f = info["funcs"][fi]
        key = "0x%08x" % f["start"]
        hint = None
        for s in sorted(set(by_func[fi])):
            text = s.strip()
            if text:
                hint = text.splitlines()[0][:80]
                break
        hints[key] = hint
        out["functions_with_strings"][key] = {
            "size": f["size"],
            "strings": sorted(set(by_func[fi]))[:8],
        }
    for f in info["funcs"]:
        key = "0x%08x" % f["start"]
        out["functions"].append({
            "start": f["start"],
            "end": f["end"],
            "size": f["size"],
            "name_hint": hints.get(key),
        })
    print(json.dumps({k: v for k, v in out.items()
                      if k != "functions_with_strings"}, indent=2, ensure_ascii=False))
    print("\n带字符串线索的函数（前 40 个）：")
    for addr, meta in list(out["functions_with_strings"].items())[:40]:
        print("%s size=0x%x  %s" % (addr, meta["size"], " | ".join(meta["strings"][:3])))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print("\nJSON: %s" % args.json)


if __name__ == "__main__":
    main()
