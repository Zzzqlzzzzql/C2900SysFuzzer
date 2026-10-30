#!/usr/bin/env python3
"""在 -fuzz 快照轮次中验证宿主 <-> C2900 guest 的 TCP 收发。

流程（与 AFLNet -Z 的每轮语义一致）：
  0x02 loadvm + 重置覆盖率 -> 宿主 TCP 连接/发包/收包 -> 0x03 收本轮覆盖率

示例：
  # SSH：读服务端 banner，再发项目自带的 KEXINIT 种子
  ./verify_net_roundtrip.py --host 172.16.100.2 --port 22 \
      --payload-file protocols/ssh/seeds/ssh_kexinit.raw --expect SSH-

  # HTTP：发一个 GET
  ./verify_net_roundtrip.py --host 172.16.100.2 --port 80 \
      --request $'GET / HTTP/1.0\r\nHost: 172.16.100.2\r\n\r\n' --expect HTTP/
"""
import argparse
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fuzz_client import Shm, EDGE_KEY, EXIT_KEY, EDGE_SIZE, send_cmd, wait_ready  # noqa: E402


def edge_stats(edge):
    data = edge.bytes()
    return sum(1 for b in data if b), sum(data)


def recv_until_idle(sock, timeout):
    """读取到超时/对端关闭为止；返回所有收到的字节。"""
    sock.settimeout(timeout)
    chunks = []
    while True:
        try:
            data = sock.recv(4096)
        except socket.timeout:
            break
        except (ConnectionResetError, OSError):
            break
        if not data:
            break
        chunks.append(data)
    return b"".join(chunks)


def one_round(host, port, payload, connect_timeout, read_timeout, settle):
    """发 0x02 -> TCP 交互 -> 发 0x03；返回 (结果 dict)。"""
    result = {
        "loadvm": None,
        "connected": False,
        "banner": b"",
        "response": b"",
        "error": "",
        "end": None,
    }
    result["loadvm"] = send_cmd(0x02)
    if result["loadvm"] != 1:
        result["error"] = "0x02 status != 1"
        return result

    # 0x02 ack 已等待 wait_snapshot_restore；再留一点余量给 guest 网卡/ARP。
    if settle:
        time.sleep(settle)

    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=connect_timeout)
        result["connected"] = True
        # SSH 等服务端先说话；HTTP 这里通常为空。
        result["banner"] = recv_until_idle(sock, read_timeout)
        if payload:
            sock.sendall(payload)
            result["response"] = recv_until_idle(sock, read_timeout)
    except Exception as exc:  # noqa: BLE001 - 验证脚本，记录任何网络错误
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    result["end"] = send_cmd(0x03)
    return result


def preview(data, limit=120):
    text = data.decode("latin1", "replace").replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit] + ("..." if len(text) > limit else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="172.16.100.2")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--payload-file")
    ap.add_argument("--request", default="")
    ap.add_argument("--expect", default="", help="响应中应出现的子串")
    ap.add_argument("--connect-timeout", type=float, default=5.0)
    ap.add_argument("--read-timeout", type=float, default=1.5)
    ap.add_argument("--settle", type=float, default=0.3)
    ap.add_argument("--save", action="store_true", help="先发 0x01 保存当前快照")
    ap.add_argument("--skip-baseline", action="store_true")
    args = ap.parse_args()

    payload = b""
    if args.payload_file:
        with open(args.payload_file, "rb") as f:
            payload = f.read()
    if args.request:
        payload += args.request.encode()

    if not wait_ready(60):
        print("FAIL: forkserver IPC 未就绪（QEMU 未启用 -fuzz？）", file=sys.stderr)
        return 2

    edge = Shm(EDGE_KEY, EDGE_SIZE)
    exit_shm = Shm(EXIT_KEY, 4)
    exit_val = int.from_bytes(bytes(exit_shm.buf[:4]), sys.byteorder)
    try:
        if args.save:
            status = send_cmd(0x01)
            print(f"0x01 save snapshot -> status={status}")
            if status != 1:
                return 2
            time.sleep(0.5)

        baseline_edges = None
        if not args.skip_baseline:
            st2 = send_cmd(0x02)
            if args.settle:
                time.sleep(args.settle)
            st3 = send_cmd(0x03)
            baseline_edges = edge_stats(edge)[0]
            print(f"baseline(no input): 0x02={st2} 0x03={st3} edges={baseline_edges}")

        rounds = []
        for i in range(args.rounds):
            r = one_round(args.host, args.port, payload,
                          args.connect_timeout, args.read_timeout, args.settle)
            edges, bsum = edge_stats(edge)
            exit_val = int.from_bytes(bytes(exit_shm.buf[:4]), sys.byteorder)
            rx = r["banner"] + r["response"]
            ok = r["connected"] and r["loadvm"] == 1 and r["end"] == 1
            if args.expect:
                ok = ok and (args.expect.encode() in rx)
            rounds.append((ok, edges, rx, r))
            print(f"round {i + 1}: 0x02={r['loadvm']} connected={r['connected']} "
                  f"banner={len(r['banner'])}B response={len(r['response'])}B "
                  f"edges={edges} sum={bsum} end={r['end']} exit=0x{exit_val:08x}")
            if r["banner"]:
                print(f"  banner: {preview(r['banner'])}")
            if r["response"]:
                print(f"  response: {preview(r['response'])}")
            if r["error"]:
                print(f"  error: {r['error']}")

        ok_rounds = sum(1 for ok, _, _, _ in rounds if ok)
        recv_rounds = sum(1 for _, _, rx, _ in rounds if rx)
        edge_values = [e for _, e, _, _ in rounds]
        if baseline_edges is None:
            baseline_edges = min(edge_values) if edge_values else 0
        delta = [e - baseline_edges for e in edge_values]
        print()
        print(f"summary: ok_rounds={ok_rounds}/{args.rounds} recv_rounds={recv_rounds}/{args.rounds} "
              f"baseline={baseline_edges} edges={edge_values} delta={delta} "
              f"exit=0x{exit_val:08x}")
        if args.expect:
            print(f"expect: {args.expect!r}")

        # 判定：至少一轮连上并收到数据；AFLNet 能跑的最低要求。
        # 覆盖率增长只报告，不作为硬失败（服务端可能已有后台执行）。
        if ok_rounds == 0 or recv_rounds == 0:
            print("FAIL: 没有一轮完成 TCP 连接并收到数据")
            return 1
        if exit_val != 0xDEADBEEF:
            print("FAIL: guest 在轮次中退出/崩溃（exit != 0xdeadbeef）")
            return 1
        print("PASS: 宿主 <-> guest TCP 收发在 -fuzz 快照轮次中可用")
        return 0
    finally:
        edge.close()
        exit_shm.close()


if __name__ == "__main__":
    sys.exit(main())
