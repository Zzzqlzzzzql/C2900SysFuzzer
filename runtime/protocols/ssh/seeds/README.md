SSH 种子需要包含真实握手（C2900 使用旧 KEX/cipher：
diffie-hellman-group14-sha1 / group1-sha1、ssh-rsa、aes128-cbc、hmac-sha1）。

已生成一个只含明文握手的初始种子：

```text
ssh_kexinit.raw = 客户端版本行 "SSH-2.0-OpenSSH_9.0\r\n"
                + SSH_MSG_KEXINIT（算法与固件支持列表有交集）
```

生成命令（不连接目标、不启动 QEMU）：

```bash
python3 scripts/gen_ssh_seed.py --out runtime/protocols/ssh/seeds/ssh_kexinit.raw
```

该种子用于 AFLNet `-P SSH` 的 dry run 和初始变异；要覆盖认证/通道状态，
仍建议抓一次真实客户端会话（见上级 README）：

```bash
sudo tcpdump -i <tap> -w /tmp/ssh.pcap 'tcp port 22'
# Wireshark Follow TCP Stream -> Raw，保存客户端→服务器方向
cp <客户端字节流> runtime/protocols/ssh/seeds/ssh_login.raw
```

注意：NEWKEYS 之后报文加密，AFLNet 的 SSH 解析器无法再按明文
length/message-code 解析服务端响应。
