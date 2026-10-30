#!/usr/bin/env python3
"""AFLNet 兼容性测试用的最小 HTTP 服务器。

它不是 C2900，也不替代 C2900；只用于验证 AFLNet ↔ QEMU forkserver 的
FIFO/shm 链路：AFLNet 发包、收包、每轮读取 QEMU 写出的 bitmap。

用法：
  ./dummy_http_server.py [port]
"""
import socket
import sys

port = int(sys.argv[1]) if len(sys.argv) > 1 else 19080
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port))
srv.listen(32)
print(f"dummy http server listening on 127.0.0.1:{port}", flush=True)

while True:
    conn, _ = srv.accept()
    try:
        conn.settimeout(1.0)
        try:
            conn.recv(65536)
        except OSError:
            pass
        conn.sendall(b"HTTP/1.1 200 OK\r\n"
                     b"Content-Length: 2\r\n"
                     b"Connection: close\r\n\r\nOK")
    finally:
        conn.close()
