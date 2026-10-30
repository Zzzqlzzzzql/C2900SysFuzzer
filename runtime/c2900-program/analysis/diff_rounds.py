#!/usr/bin/env python3
"""对 QEMU fuzz 的 debug block 日志做分轮差分。

用法:
  ./diff_rounds.py --log Logs/coverage.log --base "round 1" --test "round 2" [--control "round 3"]
输出 test - base - control 的独有 TB（canonical 物理地址）以及聚类范围。
"""
import argparse
import re
from collections import defaultdict

CSV_RE = re.compile(r"\[DEBUG\]\s+(0x[0-9a-fA-F]+),(0x[0-9a-fA-F]+),(0x[0-9a-fA-F]+),0x([0-9a-fA-F]+),(\d+),(\d+)")


def parse(path):
    sections = defaultdict(set)
    cur = None
    with open(path, "r", errors="replace") as f:
        for line in f:
            if "coverage captured at snapshot save" in line:
                cur = "snapshot"
                continue
            m = re.search(r"coverage after fuzz round (\d+)", line)
            if m:
                cur = "round %s" % m.group(1)
                continue
            if cur:
                m = CSV_RE.search(line)
                if m:
                    sections[cur].add(int(m.group(1), 16))
    return sections


def cluster(addrs, gap=0x400):
    addrs = sorted(addrs)
    out = []
    for a in addrs:
        if not out or a - out[-1][1] > gap:
            out.append([a, a])
        else:
            out[-1][1] = a
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--control", default=None)
    ap.add_argument("--gap", type=lambda x: int(x, 0), default=0x400)
    ap.add_argument("--dump", default=None)
    args = ap.parse_args()

    secs = parse(args.log)
    for k in sorted(secs):
        print("%-10s TBs=%d" % (k, len(secs[k])))
    test = secs[args.test]
    base = secs[args.base]
    ctrl = secs[args.control] if args.control else set()
    unique = test - base - ctrl
    common_bg = test & ctrl
    print("\n%s unique = %d (test %d - base %d - control %d); test&control=%d" % (
        args.test, len(unique), len(test), len(base), len(ctrl), len(common_bg)))
    cl = cluster(unique, args.gap)
    print("clusters (gap=0x%x): %d" % (args.gap, len(cl)))
    for a, b in cl[:40]:
        print("  0x%08x - 0x%08x  (%d TBs)" % (a, b, sum(1 for x in unique if a <= x <= b)))
    if args.dump:
        with open(args.dump, "w") as f:
            for a in sorted(unique):
                f.write("0x%08x\n" % a)
        print("unique addresses -> %s" % args.dump)


if __name__ == "__main__":
    main()
