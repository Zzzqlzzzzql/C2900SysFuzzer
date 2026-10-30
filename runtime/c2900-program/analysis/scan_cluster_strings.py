#!/usr/bin/env python3
"""在指定的 TB 地址聚集区反汇编，找 .rodata/.data 字符串直接引用。"""
import argparse
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS64, CS_MODE_BIG_ENDIAN

SEGS = [
    # file, va, size
    (0x60, 0x81000000, 0x4950000),
    (0x4950060, 0x85956000, 0x1ed1020),
    (0x68270f0, 0x87827090, 0xdf0910),
]


def v2f(va):
    # 链接 VA（KSEG0 0x8xxx）或运行时 VA（0x2xxx -> phys = va - 0x20000000）
    if 0x20000000 <= va < 0x30000000:
        va -= 0x20000000
        for fo, sva, sz in SEGS:
            phys = sva - 0x80000000
            if phys <= va < phys + sz:
                return fo + (va - phys)
        return None
    for fo, sva, sz in SEGS:
        if sva <= va < sva + sz:
            return fo + (va - sva)
    return None


def read_cstr(d, fo, maxlen=96):
    if fo is None or fo >= len(d):
        return None
    end = d.find(b"\0", fo, min(len(d), fo + maxlen))
    if end < 0:
        return None
    s = d[fo:end]
    if len(s) < 3 or any(b < 32 or b >= 127 for b in s):
        return None
    return s.decode("ascii")


def cluster(addrs, gap):
    out = []
    for a in sorted(addrs):
        if not out or a - out[-1][1] > gap:
            out.append([a, a])
        else:
            out[-1][1] = a
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addrs", required=True, help="每行一个物理地址（来自 diff_rounds --dump）")
    ap.add_argument("--firmware", required=True)
    ap.add_argument("--gap", type=lambda x: int(x, 0), default=0x200)
    ap.add_argument("--margin", type=lambda x: int(x, 0), default=0x80)
    ap.add_argument("--filter", default=None, help="只打印包含该子串的字符串")
    args = ap.parse_args()

    d = open(args.firmware, "rb").read()
    addrs = [int(x, 16) for x in open(args.addrs) if x.strip()]
    cl = cluster(addrs, args.gap)
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 | CS_MODE_BIG_ENDIAN)
    md.detail = True
    md.skipdata = True
    hits = []
    for lo, hi in cl:
        lo2 = max(0x01000000, lo - args.margin)
        hi2 = hi + args.margin
        foff = 0x60 + (lo2 - 0x01000000)
        size = hi2 - lo2
        code = d[foff:foff + size]
        regs = {}
        for ins in md.disasm(code, lo2 + 0x80000000):  # 链接 VA
            if ins.id == 0:
                continue
            try:
                ops = ins.operands
            except Exception:
                continue
            m = ins.mnemonic
            if m == "lui" and len(ops) == 2 and ops[0].type == 1:
                regs[ins.reg_name(ops[0].reg)] = (ops[1].imm & 0xffff) << 16
            elif m in ("addiu", "ori") and len(ops) == 3 and ops[0].type == 1 and ops[1].type == 1:
                dst = ins.reg_name(ops[0].reg)
                src = ins.reg_name(ops[1].reg)
                if src in regs:
                    imm = ops[2].imm
                    if m == "addiu" and imm >= (1 << 31):
                        imm -= 1 << 32
                    val = (regs[src] + imm) & 0xffffffff
                    regs[dst] = val
                    s = read_cstr(d, v2f(val))
                    if s:
                        pc = ins.address
                        hits.append((lo, pc, val, s))
                else:
                    regs.pop(dst, None)
            else:
                try:
                    for rid in ins.regs_access()[3]:
                        regs.pop(md.reg_name(rid), None)
                except Exception:
                    pass
    for lo, pc, val, s in hits:
        if args.filter and args.filter.lower() not in s.lower():
            continue
        print("cluster=0x%08x pc=0x%08x str_va=0x%08x  %r" % (lo, pc, val, s))


if __name__ == "__main__":
    main()
