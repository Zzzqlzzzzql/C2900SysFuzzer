#!/usr/bin/env python3
"""C2900 非网络程序插桩验证：M9 启动解压 stub。

流程：
  1. 用项目 qemu-system-mips64（含 snapshot_trigger 支持）启动 M9 外层镜像；
  2. 当 guest 执行到 stub 入口 VA 0x81000000（phys 0x01000000）时，
     fuzz/coverage.c 自动通过 /tmp/qemu_fuzz_auto 通知 forkserver 保存快照
     （snapshot_name=snap_stub）；
  3. 每轮 0x02 loadvm 回到 stub 入口，0x03 收集该轮 block/edge 覆盖率；
  4. 校验 TB 集一致、边数稳定、所有地址落在 stub .text 物理范围
     0x01000000-0x01018000，并输出地址→外层镜像文件偏移的映射。

全程不需要 guest 网络；QEMU 以 `-nic none` 启动（C2900_NIC_ARGS=none）。

用法:
  ./verify_stub.py                 # 默认 5 轮
  ./verify_stub.py --rounds 3
  ./verify_stub.py --firmware <M9 外层镜像> --run-dir /tmp/...
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
START = os.path.join(ROOT, "runtime", "start_c2900_fuzz.sh")
CLIENT = os.path.join(ROOT, "runtime", "fuzz_client.py")
DEFAULT_FW = os.path.join(ROOT, "vendor", "c2900", "firmware",
                          "c2900-universalk9-mz.SPA.150-1.M9.bin")
DEFAULT_CFG = os.path.join(HERE, "config", "stub-snapshot-m9.json")
DEFAULT_QEMU = os.path.join(ROOT, "vendor", "qemu", "bin", "qemu-system-mips64")

STUB_VA = 0x81000000
STUB_PHYS = 0x01000000
STUB_OUTER_OFF = 0x194         # M9 外层 ELF PT_LOAD 文件偏移
STUB_SIZE = 0x18000
SNAPSHOT_NAME = "snap_stub"

TRIGGER_RE = re.compile(
    r"snapshot trigger hit: virt=(0x[0-9a-fA-F]+) phys=(0x[0-9a-fA-F]+) "
    r"canon=(0x[0-9a-fA-F]+) target=(0x[0-9a-fA-F]+)")
CSV_RE = re.compile(
    r"\[DEBUG\]\s+(0x[0-9a-fA-F]+),(0x[0-9a-fA-F]+),(0x[0-9a-fA-F]+),"
    r"0x([0-9a-fA-F]+),(\d+),(\d+)")
ROUND_HDR = re.compile(r"coverage after fuzz round (\d+)")
ROUND_OUT = re.compile(r"status=(\d+),\s*([\d.]+)s,\s*edges\s+(\d+)\s*->\s*(\d+)")
END_OUT = re.compile(r"status=(\d+),\s*([\d.]+)s,\s*edges=\((\d+),\s*(\d+)\),"
                     r"\s*exit=0x([0-9a-fA-F]+)")


def log(msg):
    print(msg, flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path):
    try:
        with open(path, "r", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def find_running_qemu():
    try:
        out = subprocess.run(
            ["pgrep", "-af", "vendor/qemu/bin/qemu-system-mips64"],
            capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in out.splitlines() if line.strip()]


def run_client(action, timeout):
    r = subprocess.run([sys.executable, CLIENT, action, "--timeout", str(timeout)],
                       capture_output=True, text=True, timeout=timeout + 30)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def parse_round_coverage(path):
    """返回 {round_no: {canon: {virt, phys, exec, insn}}}；忽略 snapshot 段。"""
    rounds = {}
    cur = None
    for line in read_text(path).splitlines():
        m = ROUND_HDR.search(line)
        if m:
            cur = int(m.group(1))
            rounds.setdefault(cur, {})
            continue
        if "coverage captured at snapshot save" in line:
            cur = None
            continue
        if cur is None:
            continue
        m = CSV_RE.search(line)
        if m:
            canon = int(m.group(1), 16)
            rounds[cur][canon] = {
                "virt": m.group(2),
                "phys": m.group(3),
                "exec": int(m.group(5)),
                "insn": int(m.group(6)),
            }
    return rounds


def main():
    ap = argparse.ArgumentParser(description="M9 启动 stub 非网络程序插桩验证")
    ap.add_argument("--firmware", default=os.environ.get("C2900_PROGRAM_FIRMWARE", DEFAULT_FW))
    ap.add_argument("--config", default=os.environ.get("C2900_PROGRAM_CONFIG", DEFAULT_CFG))
    ap.add_argument("--qemu", default=os.environ.get("C2900_PROGRAM_QEMU", DEFAULT_QEMU))
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--trigger-timeout", type=float, default=120.0)
    ap.add_argument("--client-timeout", type=float, default=120.0)
    ap.add_argument("--run-dir", default=None)
    args = ap.parse_args()

    for label, path in (("QEMU", args.qemu), ("M9 外层镜像", args.firmware),
                        ("配置", args.config)):
        if not os.path.isfile(path):
            sys.exit("缺少%s：%s" % (label, path))
    if not os.path.isfile(START) or not os.path.isfile(CLIENT):
        sys.exit("缺少 runtime 启动脚本或 fuzz_client.py")

    running = find_running_qemu()
    if running:
        sys.exit("检测到正在运行的 QEMU（forkserver/FIFO/shm 是固定路径）：\n  " +
                 "\n  ".join(running) + "\n请先停止：runtime/start_c2900_fuzz.sh stop")

    def parse_hex(v):
        return int(v, 16) if isinstance(v, str) else int(v or 0)

    with open(args.config) as f:
        cfg = json.load(f)
    if not cfg.get("auto_snapshot"):
        sys.exit("配置必须设置 auto_snapshot=true（snapshot_trigger 依赖自动快照管道）")
    if parse_hex(cfg.get("snapshot_trigger", "0")) != STUB_VA:
        sys.exit("配置 snapshot_trigger 必须是 0x%x" % STUB_VA)
    snapshot_name = cfg.get("snapshot_name", "fuzz_snapshot")
    cov_start = parse_hex(cfg.get("coverage_start", "0"))
    cov_end = parse_hex(cfg.get("coverage_end", "0"))
    if not (cov_start <= STUB_PHYS and cov_end >= STUB_PHYS + STUB_SIZE):
        sys.exit("coverage 范围没有覆盖 stub 物理区 0x%x-0x%x" %
                 (STUB_PHYS, STUB_PHYS + STUB_SIZE))

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.run_dir or os.path.join(HERE, "runs", "%s-stub-m9" % ts)
    os.makedirs(run_dir, exist_ok=True)
    cov_log = os.path.join(run_dir, "Logs", "coverage.log")
    fs_log = os.path.join(run_dir, "Logs", "forkserver.log")
    result_path = os.path.join(run_dir, "result.json")

    env = os.environ.copy()
    env.update({
        "C2900_KERNEL": os.path.abspath(args.firmware),
        "C2900_FUZZ_CONFIG": os.path.abspath(args.config),
        "C2900_FUZZ_RUNDIR": os.path.abspath(run_dir),
        "C2900_NIC_ARGS": "none",       # 本用例不依赖 guest 网络
        "C2900_QEMU": os.path.abspath(args.qemu),
    })

    result = {
        "test": "c2900-nonet-program-stub",
        "timestamp": ts,
        "run_dir": run_dir,
        "network": "none (C2900_NIC_ARGS=none)",
        "firmware": {"path": os.path.abspath(args.firmware),
                     "sha256": sha256_file(args.firmware)},
        "qemu": {"path": os.path.abspath(args.qemu),
                 "sha256": sha256_file(args.qemu)},
        "config": {"path": os.path.abspath(args.config), "effective": cfg},
        "stub": {"va": "0x%08x" % STUB_VA, "phys": "0x%08x" % STUB_PHYS,
                 "outer_elf_offset": "0x%x" % STUB_OUTER_OFF,
                 "size": "0x%x" % STUB_SIZE,
                 "coverage_range": ["0x%08x" % cov_start, "0x%08x" % cov_end]},
        "rounds": [],
        "checks": {},
    }
    checks = result["checks"]

    started = False
    try:
        log("== C2900 非网络程序插桩验证（M9 启动 stub） ==")
        log("固件: %s" % args.firmware)
        log("输出: %s" % run_dir)
        r = subprocess.run([START, "start"], env=env, capture_output=True,
                           text=True, timeout=90)
        if r.returncode != 0:
            raise RuntimeError("启动失败：\n%s\n%s" % (r.stdout, r.stderr))
        started = True
        log("QEMU 已启动，等待 stub 入口触发自动快照 ...")

        deadline = time.time() + args.trigger_timeout
        trigger = None
        while time.time() < deadline:
            cov = read_text(cov_log)
            fs = read_text(fs_log)
            m = TRIGGER_RE.search(cov)
            if m:
                trigger = {
                    "virt": m.group(1), "phys": m.group(2),
                    "canon": m.group(3), "target": m.group(4),
                }
                if "snapshot saved: %s" % snapshot_name in fs:
                    break
            time.sleep(0.3)
        else:
            raise RuntimeError(
                "等待 snapshot_trigger/自动快照超时。请确认 QEMU 已用最新源码重建"
                "（./scripts/build-qemu.sh），且配置 auto_snapshot=true、"
                "snapshot_trigger=0x%x" % STUB_VA)
        result["trigger"] = trigger
        result["snapshot"] = {"name": snapshot_name, "saved": True}
        checks["trigger_hit"] = (int(trigger["target"], 16) == STUB_VA and
                                 int(trigger["phys"], 16) == STUB_PHYS)
        checks["auto_snapshot_saved"] = True
        log("触发: virt=%s phys=%s canon=%s target=%s" %
            (trigger["virt"], trigger["phys"], trigger["canon"], trigger["target"]))
        log("快照: %s" % snapshot_name)

        for i in range(1, args.rounds + 1):
            rc, out = run_client("round", args.client_timeout)
            with open(os.path.join(run_dir, "client-round-%d.log" % i), "w") as f:
                f.write(out)
            m = ROUND_OUT.search(out)
            if not m:
                raise RuntimeError("无法解析 0x02 输出：%s" % out.strip())
            if int(m.group(1)) != 1:
                raise RuntimeError("0x02 第 %d 轮失败：%s" % (i, out.strip()))
            rc, end_out = run_client("end", args.client_timeout)
            with open(os.path.join(run_dir, "client-end-%d.log" % i), "w") as f:
                f.write(end_out)
            e = END_OUT.search(end_out)
            if not e:
                raise RuntimeError("无法解析 0x03 输出：%s" % end_out.strip())
            if int(e.group(1)) != 1:
                raise RuntimeError("0x03 第 %d 轮失败：%s" % (i, end_out.strip()))
            result["rounds"].append({
                "round": i,
                "loadvm_status": int(m.group(1)),
                "loadvm_seconds": float(m.group(2)),
                "edges_before": int(m.group(3)),
                "edges_after_loadvm": int(m.group(4)),
                "end_status": int(e.group(1)),
                "end_seconds": float(e.group(2)),
                "edges": int(e.group(3)),
                "bitmap_sum": int(e.group(4)),
                "exit_status": "0x" + e.group(5).lower(),
            })
            log("round %d: 0x02 status=1 %.2fs edges %s -> %s；0x03 edges=%s sum=%s" %
                (i, float(m.group(2)), m.group(3), m.group(4), e.group(3), e.group(4)))

        rounds_cov = parse_round_coverage(cov_log)
        tb_ok = True
        tb_identical = True
        first_set = None
        all_addrs = set()
        for i in range(1, args.rounds + 1):
            tb = rounds_cov.get(i, {})
            if not tb:
                raise RuntimeError("第 %d 轮没有解析到 debug TB 记录" % i)
            aset = set(tb)
            all_addrs |= aset
            if any(not (STUB_PHYS <= a < STUB_PHYS + STUB_SIZE) for a in aset):
                tb_ok = False
            if first_set is None:
                first_set = aset
            elif aset != first_set:
                tb_identical = False

        hot = []
        for a in all_addrs:
            meta = None
            for i in range(args.rounds, 0, -1):
                if a in rounds_cov[i]:
                    meta = rounds_cov[i][a]
                    break
            if meta:
                hot.append({"canon": "0x%08x" % a,
                            "virt": meta["virt"],
                            "phys": meta["phys"],
                            "exec_count": meta["exec"],
                            "insn_count": meta["insn"],
                            "outer_file_offset": "0x%x" %
                                (STUB_OUTER_OFF + (a - STUB_PHYS))})
        hot.sort(key=lambda x: x["exec_count"], reverse=True)
        result["coverage"] = {
            "tb_unique_total": len(all_addrs),
            "tb_first_round": len(first_set or []),
            "tb_address_ranges": {
                "min": "0x%08x" % min(all_addrs) if all_addrs else None,
                "max": "0x%08x" % max(all_addrs) if all_addrs else None,
            },
            "top_tbs": hot[:10],
        }

        edges = [r["edges"] for r in result["rounds"]]
        tol = max(4, (max(edges) // 10) if edges else 0)
        checks["all_rounds_status_ok"] = all(
            r["loadvm_status"] == 1 and r["end_status"] == 1 for r in result["rounds"])
        checks["edges_nonzero"] = bool(edges) and min(edges) > 0
        checks["edges_stable"] = (max(edges) - min(edges) <= tol) if edges else False
        checks["tb_sets_identical"] = tb_identical
        checks["tb_in_stub_range"] = tb_ok
        checks["hot_loop_seen"] = any(h["exec_count"] >= 1000 for h in hot)
        result["stability"] = {"edges": edges, "tolerance": tol,
                               "min": min(edges) if edges else None,
                               "max": max(edges) if edges else None}
        result["pass"] = all(v is True for v in checks.values())
        result["notes"] = [
            "M9 外层镜像由 start_c2900_fuzz.sh 生成 e_machine=0x0008 的临时副本；原固件不改。",
            "stub 入口物理地址 0x01000000 对应外层镜像文件偏移 0x194（PT_LOAD）。",
            "snapshot_trigger 是本次对 fuzz/coverage.c 的实现：IOS 模式下命中地址"
            "经 /tmp/qemu_fuzz_auto 通知 forkserver 自动 savevm。",
            "轮内 0x02 等待 100ms、0x03 等待 300ms；stub 的解压主循环迭代次数随"
            "调度变化，但执行的 TB 集一致。",
        ]
    except Exception as e:
        result["pass"] = False
        result["error"] = str(e)
        log("\nFAIL: %s" % e)
        if started:
            for p in (cov_log, fs_log):
                t = read_text(p).replace("\r", "").splitlines()[-12:]
                if t:
                    log("--- %s 末尾 ---" % os.path.basename(p))
                    for line in t:
                        log("  " + line[:200])
    finally:
        if started:
            subprocess.run([START, "stop"], env=env, capture_output=True,
                           text=True, timeout=90)
        with open(result_path, "w") as f:
            json.dump(result, f, indent=4)
            f.write("\n")

    if result.get("pass"):
        log("\nPASS: M9 stub 自动快照/多轮 loadvm/程序范围覆盖均通过")
        log("轮次边数: %s；TB 集: %d 个，是否一致: %s" % (
            result["stability"]["edges"],
            result["coverage"]["tb_unique_total"],
            checks["tb_sets_identical"]))
        log("结果: %s" % result_path)
        return 0
    log("\n未通过，结果: %s" % result_path)
    return 1


if __name__ == "__main__":
    sys.exit(main())
