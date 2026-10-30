#!/usr/bin/env python3
"""独立读取 QEMU fuzz 共享内存，用于验证覆盖率是否真实、可复现、可被 fuzzer 使用。

它只 attach QEMU 创建的 shm（不创建），因此可以在 QEMU 运行期间从任意进程
观测同一块 64KB edge bitmap。

用法：
  ./coverage_probe.py wait [timeout]      等待 shm 0x2000/0x2001
  ./coverage_probe.py stats               打印 edges/sum/hash/exit
  ./coverage_probe.py top [N]             打印命中次数最高的 N 个 edge
  ./coverage_probe.py dump <file>         保存 65536 字节 bitmap + <file>.json
  ./coverage_probe.py diff <a> <b>        比较两份 dump（新增/消失/变化/相同）
  ./coverage_probe.py watch [interval] [count]  周期性打印 stats

判据示例：
  - 启动前 edges=0；guest 开始执行后 edges>0 且 sum 增长；
  - monitor stop 后连续两次 stats 的 hash 完全一致；
  - monitor cont 后 hash 变化；
  - 0x02 后 edges/hash 回到很小/空（exclude_baseline=true）。
"""
import ctypes
import hashlib
import json
import os
import struct
import sys
import time

EDGE_KEY = 0x2000
EXIT_KEY = 0x2001
EDGE_SIZE = 65536
CTL = "/tmp/qemu_fuzz_ctl"
ST = "/tmp/qemu_fuzz_st"

libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.shmat.restype = ctypes.c_void_p


class Shm:
    def __init__(self, key, size):
        self.id = libc.shmget(key, size, 0)
        if self.id < 0:
            raise OSError(ctypes.get_errno(), f"shmget(0x{key:x})")
        self.addr = libc.shmat(self.id, None, 0)
        if self.addr in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_errno(), f"shmat(0x{key:x})")
        self.size = size
        self.buf = (ctypes.c_uint8 * size).from_address(self.addr)

    def data(self):
        return bytes(self.buf)

    def close(self):
        libc.shmdt(ctypes.c_void_p(self.addr))


def open_probe():
    edge = Shm(EDGE_KEY, EDGE_SIZE)
    exit_shm = Shm(EXIT_KEY, 4)
    exit_u32 = ctypes.cast(exit_shm.addr, ctypes.POINTER(ctypes.c_uint32))
    return edge, exit_shm, exit_u32


def stats(edge, exit_u32):
    data = edge.data()
    return {
        "edges": sum(1 for b in data if b),
        "sum": sum(data),
        "hash": hashlib.blake2b(data, digest_size=8).hexdigest(),
        "exit": f"0x{exit_u32[0]:08x}",
    }


def fmt_stats(s):
    return (f"edges={s['edges']} sum={s['sum']} "
            f"hash={s['hash']} exit={s['exit']}")


def wait_ready(timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            edge, exit_shm, _ = open_probe()
            edge.close()
            exit_shm.close()
            return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    action = sys.argv[1]

    if action == "wait":
        timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 30
        ok = wait_ready(timeout)
        print("shm ready" if ok else "shm timeout")
        return 0 if ok else 1

    try:
        edge, exit_shm, exit_u32 = open_probe()
    except OSError as e:
        print(f"无法 attach shm：{e}（QEMU 未启动 -fuzz？）", file=sys.stderr)
        return 1

    try:
        if action == "stats":
            print(fmt_stats(stats(edge, exit_u32)))
            return 0

        if action == "top":
            n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
            data = edge.data()
            top = sorted(((v, i) for i, v in enumerate(data) if v),
                         reverse=True)[:n]
            print("count edge_index")
            for count, idx in top:
                print(f"{count:5d} 0x{idx:04x}")
            return 0

        if action == "dump":
            if len(sys.argv) < 3:
                print("用法：coverage_probe.py dump <file>", file=sys.stderr)
                return 2
            path = sys.argv[2]
            data = edge.data()
            with open(path, "wb") as f:
                f.write(data)
            meta = stats(edge, exit_u32)
            meta["file"] = os.path.abspath(path)
            meta["size"] = len(data)
            meta["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(path + ".json", "w") as f:
                json.dump(meta, f, indent=2)
            print(f"dumped {len(data)} bytes -> {path}")
            print(fmt_stats(meta))
            return 0

        if action == "diff":
            if len(sys.argv) < 4:
                print("用法：coverage_probe.py diff <a> <b>", file=sys.stderr)
                return 2
            a = open(sys.argv[2], "rb").read()
            b = open(sys.argv[3], "rb").read()
            if len(a) != EDGE_SIZE or len(b) != EDGE_SIZE:
                print("dump 大小不是 65536", file=sys.stderr)
                return 2
            new_edges = sum(1 for x, y in zip(a, b) if x == 0 and y != 0)
            lost_edges = sum(1 for x, y in zip(a, b) if x != 0 and y == 0)
            changed = sum(1 for x, y in zip(a, b) if x != y)
            same = EDGE_SIZE - changed
            print(f"identical={a == b}")
            print(f"new_edges={new_edges}")
            print(f"lost_edges={lost_edges}")
            print(f"changed_bytes={changed}")
            print(f"same_bytes={same}")
            print(f"sum_a={sum(a)} sum_b={sum(b)}")
            print(f"hash_a={hashlib.blake2b(a, digest_size=8).hexdigest()}")
            print(f"hash_b={hashlib.blake2b(b, digest_size=8).hexdigest()}")
            return 0

        if action == "watch":
            interval = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
            count = int(sys.argv[3]) if len(sys.argv) > 3 else 0
            i = 0
            while count <= 0 or i < count:
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] {fmt_stats(stats(edge, exit_u32))}", flush=True)
                i += 1
                time.sleep(interval)
            return 0

        print(f"未知动作：{action}", file=sys.stderr)
        return 2
    finally:
        edge.close()
        exit_shm.close()


if __name__ == "__main__":
    sys.exit(main())
