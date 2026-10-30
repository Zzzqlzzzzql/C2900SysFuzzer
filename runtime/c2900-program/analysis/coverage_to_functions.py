#!/usr/bin/env python3
"""把 QEMU fuzz 的 debug block 日志映射到 monlib 函数。

用法:
  ./coverage_to_functions.py --log <Logs/coverage.log> --monlib <monlib.json> [--round N]
"""
import argparse
import bisect
import json
import re

CSV_RE = re.compile(r"\[DEBUG\]\s+(0x[0-9a-fA-F]+),(0x[0-9a-fA-F]+),(0x[0-9a-fA-F]+),0x([0-9a-fA-F]+),(\d+),(\d+)")


def load_funcs(path):
    d = json.load(open(path))
    funcs = d["functions"]
    starts = [f["start"] for f in funcs]
    return d, funcs, starts


def func_at(funcs, starts, addr):
    i = bisect.bisect_right(starts, addr) - 1
    if i >= 0 and funcs[i]["start"] <= addr < funcs[i]["end"]:
        return funcs[i]
    return None


def parse(log, module_start, module_end):
    sections = []
    cur = None
    with open(log, "r", errors="replace") as f:
        for line in f:
            if "coverage captured at snapshot save" in line:
                cur = {"label": "snapshot", "tbs": []}
                sections.append(cur)
                continue
            m = re.search(r"coverage after fuzz round (\d+)", line)
            if m:
                cur = {"label": "round %s" % m.group(1), "tbs": []}
                sections.append(cur)
                continue
            if cur is not None:
                m = CSV_RE.search(line)
                if m:
                    canon = int(m.group(1), 16) & 0xffffffffffffffff
                    virt = int(m.group(2), 16) & 0xffffffffffffffff
                    phys = int(m.group(3), 16) & 0xffffffffffffffff
                    if canon >= 0xffffffff80000000:
                        canon &= 0xffffffff
                    cur["tbs"].append((canon, virt, phys, int(m.group(5))))
    # 只保留目标模块范围内的 TB
    for s in sections:
        s["in_module"] = [t for t in s["tbs"] if module_start <= t[0] < module_end]
    return sections


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--monlib", required=True)
    ap.add_argument("--round", default=None, help="只看某一轮（snapshot/round 1/...）")
    args = ap.parse_args()

    meta, funcs, starts = load_funcs(args.monlib)
    mod_start = meta["text_start"]
    mod_end = mod_start + meta["text_size"]
    sections = parse(args.log, mod_start, mod_end)

    for s in sections:
        if args.round and s["label"] != args.round:
            continue
        hits = {}
        for canon, virt, phys, exec_count in s["in_module"]:
            f = func_at(funcs, starts, canon)
            if f is None:
                key = "0x%08x(no-pdr)" % canon
                hint = None
            else:
                key = "0x%08x" % f["start"]
                hint = f.get("name_hint")
            e = hits.setdefault(key, {"hint": hint, "count": 0, "addrs": set()})
            e["count"] += exec_count
            e["addrs"].add(canon)
        print("== %s: module TB entries=%d, unique=%d, functions=%d" % (
            s["label"], len(s["in_module"]),
            len(set(t[0] for t in s["in_module"])), len(hits)))
        named = sorted((k, v) for k, v in hits.items() if v["hint"])
        for k, v in named[:30]:
            print("  %s  exec=%d addrs=%d  %s" % (k, v["count"], len(v["addrs"]), v["hint"]))
        unnamed = [k for k, v in hits.items() if not v["hint"]]
        print("  unnamed functions: %d %s" % (len(unnamed), " ".join(sorted(unnamed)[:12])))
        print()


if __name__ == "__main__":
    main()
