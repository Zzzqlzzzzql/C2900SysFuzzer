#!/usr/bin/env python3
"""M9 外层启动 stub 的离线反汇编 + 覆盖率地址映射。

用法:
  ./stub_analysis.py --coverage-log ../evidence/stub-5round/Logs/coverage.log
  ./stub_analysis.py --firmware <M9 外层 .bin> --json /tmp/stub.json
"""
import argparse
import json
import re
from pathlib import Path

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS64, CS_MODE_BIG_ENDIAN

STUB_VA = 0x81000000
STUB_PHYS = 0x01000000
STUB_OFF = 0x194
STUB_SIZE = 0x18000

CSV_RE = re.compile(
    r"\[DEBUG\]\s+(0x[0-9a-fA-F]+),(0x[0-9a-fA-F]+),(0x[0-9a-fA-F]+),"
    r"0x([0-9a-fA-F]+),(\d+),(\d+)")
ROUND_HDR = re.compile(r"coverage after fuzz round (\d+)")


def parse_last_round(path):
    rounds = {}
    cur = None
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = ROUND_HDR.search(line)
            if m:
                cur = int(m.group(1))
                rounds.setdefault(cur, {})
                continue
            if cur is None:
                continue
            m = CSV_RE.search(line)
            if m:
                canon = int(m.group(1), 16)
                rounds[cur][canon] = {"virt": m.group(2), "phys": m.group(3),
                                      "exec": int(m.group(5)),
                                      "insn": int(m.group(6))}
    if not rounds:
        return {}, None
    n = max(rounds)
    return rounds[n], n


ROOT = Path(__file__).resolve().parents[3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--firmware", default=str(ROOT / "vendor/c2900/firmware/"
                                                 "c2900-universalk9-mz.SPA.150-1.M9.bin"))
    ap.add_argument("--coverage-log", default=str(ROOT / "runtime/"
                                                     "c2900-program/evidence/stub-5round/Logs/coverage.log"))
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    d = open(args.firmware, "rb").read()
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 | CS_MODE_BIG_ENDIAN)
    md.detail = True

    def disasm(va, n=32):
        off = STUB_OFF + (va - STUB_VA)
        out = []
        for ins in md.disasm(d[off:off + n * 4], va):
            out.append("%08x: %-8s %s" % (ins.address, ins.mnemonic, ins.op_str))
        return out

    syscalls = []
    for ins in md.disasm(d[STUB_OFF:STUB_OFF + STUB_SIZE], STUB_VA):
        if ins.mnemonic == "syscall":
            syscalls.append(ins.address)

    tb, last_round = parse_last_round(args.coverage_log)
    hot = []
    for canon, meta in tb.items():
        hot.append({"canon": canon, "virt": meta["virt"], "phys": meta["phys"],
                    "exec": meta["exec"], "insn": meta["insn"],
                    "outer_off": STUB_OFF + (canon - STUB_PHYS)})
    hot.sort(key=lambda x: x["exec"], reverse=True)

    print("== M9 外层启动 stub ==")
    print("firmware: %s" % args.firmware)
    print("外层 ELF PT_LOAD off=0x%x  VA=0x%x  size=0x%x" %
          (STUB_OFF, STUB_VA, STUB_SIZE))
    print("对应物理区: 0x%08x-0x%08x" % (STUB_PHYS, STUB_PHYS + STUB_SIZE))
    print("syscall 指令: %s" % (", ".join("0x%08x" % a for a in syscalls) or "无"))
    print("\n-- 入口 0x%08x --" % STUB_VA)
    for line in disasm(STUB_VA, 28):
        print("  " + line)
    print("\n-- 覆盖率轮次 %s：%d 个 TB，地址范围 0x%08x-0x%08x --" % (
        last_round, len(tb),
        min(tb) if tb else 0, max(tb) if tb else 0))
    for h in hot[:8]:
        print("  TB 0x%08x phys=0x%08x 外层文件偏移=0x%x exec=%d insn=%d" %
              (h["canon"], int(h["phys"], 16), h["outer_off"], h["exec"], h["insn"]))
    print("\n-- 最热 TB 的反汇编窗口 --")
    for h in hot[:4]:
        print("  @0x%08x (file 0x%x, exec=%d):" % (h["canon"], h["outer_off"], h["exec"]))
        # 从 TB 起始反汇编 16 条
        off = h["outer_off"]
        link_va = h["canon"] + 0x80000000
        for ins in md.disasm(d[off:off + 64], link_va):
            print("    %08x: %-8s %s" % (ins.address, ins.mnemonic, ins.op_str))
        print()
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"firmware": args.firmware,
                       "stub": {"va": STUB_VA, "phys": STUB_PHYS,
                                "outer_off": STUB_OFF, "size": STUB_SIZE},
                       "syscalls": syscalls,
                       "last_round": last_round,
                       "tbs": hot}, f, indent=2)
        print("JSON: %s" % args.json)


if __name__ == "__main__":
    main()
