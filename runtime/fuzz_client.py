#!/usr/bin/env python3
"""C2900 系统级 fuzz 原型的最小 forkserver 客户端。

用途：在没有 AFLNet 的情况下验证 QEMU 侧的快照/重置/覆盖率链路。
协议与 QemuSysFuzzer 的 AFLNet -Z 模式一致：

  /tmp/qemu_fuzz_ctl  写入 1 字节命令
      0x01 保存快照（包含当前覆盖率 baseline）
      0x02 loadvm + 恢复覆盖率 baseline + vm_start
      0x03 等待 wait_program_done 后输出本轮覆盖率并 ack
  /tmp/qemu_fuzz_st   QEMU 回写 4 字节 status（1 成功 / 2 失败）
  shm 0x2000          65536 字节 AFL edge bitmap
  shm 0x2001          uint32 退出状态（原型阶段为 0xDEADBEEF 哨兵）

示例：
  ./fuzz_client.py wait                 # 等待 IPC 就绪
  ./fuzz_client.py save                 # 发 0x01
  ./fuzz_client.py round                # 发 0x02，打印 ack 后的边覆盖
  ./fuzz_client.py end                  # 发 0x03
  ./fuzz_client.py verify --rounds 2    # save + 多轮 loadvm，检查 bitmap 重置
"""
import argparse
import ctypes
import os
import select
import struct
import sys
import time

CTL = "/tmp/qemu_fuzz_ctl"
ST = "/tmp/qemu_fuzz_st"
EDGE_KEY = 0x2000
EXIT_KEY = 0x2001
EDGE_SIZE = 65536
EXIT_TIMEOUT = 0xDEADBEEF

libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.shmat.restype = ctypes.c_void_p


class Shm:
    def __init__(self, key, size):
        self.key = key
        self.size = size
        self.id = libc.shmget(key, size, 0o666)
        if self.id < 0:
            raise OSError(ctypes.get_errno(), f"shmget(0x{key:x})")
        self.addr = libc.shmat(self.id, None, 0)
        if self.addr in (None, ctypes.c_void_p(-1).value):
            raise OSError(ctypes.get_errno(), f"shmat(0x{key:x})")
        self.buf = (ctypes.c_uint8 * size).from_address(self.addr)

    def bytes(self):
        return bytes(self.buf)

    def close(self):
        libc.shmdt(ctypes.c_void_p(self.addr))


def edge_stats(shm):
    data = shm.bytes()
    nonzero = sum(1 for b in data if b)
    return nonzero, sum(data)


def send_cmd(cmd, timeout=180.0):
    if not os.path.exists(CTL):
        raise FileNotFoundError(f"{CTL} 不存在（QEMU 未启用 -fuzz 或尚未启动）")
    ctl = os.open(CTL, os.O_WRONLY)
    try:
        os.write(ctl, bytes([cmd]))
    finally:
        os.close(ctl)

    st = os.open(ST, os.O_RDONLY)
    try:
        ready, _, _ = select.select([st], [], [], timeout)
        if not ready:
            raise TimeoutError(f"命令 0x{cmd:02x} 等待 status 超时")
        raw = os.read(st, 4)
    finally:
        os.close(st)
    if len(raw) != 4:
        raise RuntimeError(f"status 管道返回 {len(raw)} 字节")
    return struct.unpack("=I", raw)[0]


def wait_ready(timeout=60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(CTL) and os.path.exists(ST):
            try:
                Shm(EDGE_KEY, EDGE_SIZE).close()
                Shm(EXIT_KEY, 4).close()
                return True
            except OSError:
                pass
        time.sleep(0.2)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["wait", "save", "round", "end", "verify", "stats"])
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--timeout", type=float, default=240.0)
    args = ap.parse_args()

    if args.action == "wait":
        ok = wait_ready(args.timeout)
        print("IPC 就绪" if ok else "IPC 等待超时")
        return 0 if ok else 1

    edge = Shm(EDGE_KEY, EDGE_SIZE)
    exit_shm = Shm(EXIT_KEY, 4)
    exit_u32 = ctypes.cast(exit_shm.addr, ctypes.POINTER(ctypes.c_uint32))
    try:
        if args.action == "stats":
            print(f"edges={edge_stats(edge)} exit=0x{exit_u32[0]:08x}")
            return 0

        if args.action == "save":
            status = send_cmd(0x01, args.timeout)
            print(f"0x01 save snapshot -> status={status}")
            print(f"  bitmap: edges={edge_stats(edge)[0]}")
            return 0 if status == 1 else 1

        if args.action == "round":
            before = edge_stats(edge)[0]
            t0 = time.time()
            status = send_cmd(0x02, args.timeout)
            after = edge_stats(edge)[0]
            print(f"0x02 loadvm -> status={status}, {time.time() - t0:.2f}s, "
                  f"edges {before} -> {after}")
            return 0 if status == 1 else 1

        if args.action == "end":
            t0 = time.time()
            status = send_cmd(0x03, args.timeout)
            print(f"0x03 end round -> status={status}, {time.time() - t0:.2f}s, "
                  f"edges={edge_stats(edge)}, exit=0x{exit_u32[0]:08x}")
            return 0 if status == 1 else 1

        if args.action == "verify":
            print("保存快照 ...")
            st = send_cmd(0x01, args.timeout)
            print(f"  status={st}")
            if st != 1:
                return 1
            baseline = edge_stats(edge)
            print(f"  baseline bitmap: edges={baseline[0]} sum={baseline[1]}")

            results = []
            for i in range(args.rounds):
                st = send_cmd(0x02, args.timeout)
                mid = edge_stats(edge)
                st3 = send_cmd(0x03, args.timeout)
                end = edge_stats(edge)
                print(f"round {i + 1}: 0x02 status={st}, edges={mid[0]}, "
                      f"0x03 status={st3}, edges={end[0]}, "
                      f"exit=0x{exit_u32[0]:08x}")
                results.append(end[0])

            # 每轮无输入时，边数应在 baseline 附近小幅波动（等待窗口内 guest 仍在执行）；
            # 若出现成倍增长，通常意味着 prev_loc_exec 未重置或地址未归一化。
            stable = max(results) - min(results) <= max(16, baseline[0] // 10)
            print(f"verify: rounds={results}, baseline={baseline[0]}, "
                  f"stable={'yes' if stable else 'no'}")
            return 0 if stable else 1
    finally:
        edge.close()
        exit_shm.close()


if __name__ == "__main__":
    sys.exit(main())
