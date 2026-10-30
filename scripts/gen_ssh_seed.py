#!/usr/bin/env python3
"""gen_ssh_seed.py — 生成一个 SSH 明文握手种子（不连接目标、不启动 QEMU）。

固件静态分析得到的服务端算法（M9/M7 相同）：

  kex     : diffie-hellman-group-exchange-sha1,
            diffie-hellman-group14-sha1,diffie-hellman-group1-sha1
  hostkey : ssh-rsa
  cipher  : aes128-cbc,3des-cbc,aes192-cbc,aes256-cbc
  mac     : hmac-sha1,hmac-sha1-96,hmac-md5,hmac-md5-96

本工具只生成“客户端版本行 + SSH_MSG_KEXINIT”这一小段明文字节流，
用于 AFLNet -P SSH 的 dry run / 初始种子。真正深入认证/通道状态仍建议
抓一次真实 ssh 客户端会话（见 runtime/protocols/ssh/README.md）。

用法：
  python3 scripts/gen_ssh_seed.py \
    --out runtime/protocols/ssh/seeds/ssh_kexinit.raw

注意：KEXINIT 之后服务端的响应是明文；NEWKEYS 之后双方报文加密，
AFLNet 的 SSH 解析器无法再按 length/message-code 解析。
"""

import argparse
import os
import struct


def u32(v):
    return struct.pack(">I", v)


def ssh_string(data):
    if isinstance(data, str):
        data = data.encode()
    return u32(len(data)) + data


def name_list(items):
    return ssh_string(",".join(items))


def build_kexinit(
    kex="diffie-hellman-group14-sha1,diffie-hellman-group1-sha1",
    hostkey="ssh-rsa",
    cipher="aes128-cbc",
    mac="hmac-sha1",
    comp="none",
    cookie=b"\x00" * 16,
):
    if len(cookie) != 16:
        raise ValueError("cookie 必须是 16 字节")
    # RFC 4253: byte SSH_MSG_KEXINIT(20), cookie[16], 10 name-lists,
    # boolean first_kex_packet_follows, uint32 reserved
    payload = bytes([20]) + cookie
    payload += name_list(kex.split(","))
    payload += name_list(hostkey.split(","))
    payload += name_list(cipher.split(","))   # encryption client->server
    payload += name_list(cipher.split(","))   # encryption server->client
    payload += name_list(mac.split(","))      # MAC client->server
    payload += name_list(mac.split(","))      # MAC server->client
    payload += name_list(comp.split(","))     # compression client->server
    payload += name_list(comp.split(","))     # compression server->client
    payload += name_list([])                  # languages client->server
    payload += name_list([])                  # languages server->client
    payload += b"\x00"                        # first_kex_packet_follows
    payload += u32(0)                         # reserved

    # packet_length = padding_length(1) + payload + padding
    # 总长 (4 + packet_length) 必须是 8 的倍数（未加密、无 MAC 时块大小 8）
    padding_len = 4
    while (4 + 1 + len(payload) + padding_len) % 8 != 0:
        padding_len += 1
    packet_length = 1 + len(payload) + padding_len
    padding = bytes((i * 17 + 3) & 0xFF for i in range(padding_len))
    return u32(packet_length) + bytes([padding_len]) + payload + padding


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="runtime/protocols/ssh/seeds/ssh_kexinit.raw")
    ap.add_argument("--version", default="SSH-2.0-OpenSSH_9.0",
                    help="客户端版本行（不含 CRLF）")
    ap.add_argument("--kex", default="diffie-hellman-group14-sha1,diffie-hellman-group1-sha1")
    ap.add_argument("--hostkey", default="ssh-rsa")
    ap.add_argument("--cipher", default="aes128-cbc")
    ap.add_argument("--mac", default="hmac-sha1")
    ap.add_argument("--comp", default="none")
    args = ap.parse_args()

    version = args.version.encode() + b"\r\n"
    kexinit = build_kexinit(args.kex, args.hostkey, args.cipher, args.mac, args.comp)
    data = version + kexinit

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(data)

    print(f"written {args.out}: {len(data)} bytes")
    print(f"  version line : {args.version}")
    print(f"  KEXINIT      : {len(kexinit)} bytes (packet_length="
          f"{struct.unpack('>I', kexinit[:4])[0]})")
    print(f"  kex          : {args.kex}")
    print(f"  hostkey      : {args.hostkey}")
    print(f"  cipher/mac   : {args.cipher} / {args.mac}")
    print("\n十六进制（前 96 字节）：")
    for off in range(0, min(96, len(data)), 16):
        chunk = data[off:off + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {off:04x}  {hexs:<47}  {text}")


if __name__ == "__main__":
    main()
