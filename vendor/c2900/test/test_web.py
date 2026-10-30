#!/usr/bin/env python3

import argparse
import base64
import socket
import subprocess
import sys


HOST = "172.16.100.2"
USER = "admin"
PASSWORD = "cisco"
HTTP_URL = f"http://{HOST}/"
HTTPS_PORT = 443


def check_http():
	# 等价于 README 的手工验证命令（curl -v -u admin:cisco http://172.16.100.2）
	command = ["curl", "-sS", "-v", "-u", f"{USER}:{PASSWORD}", HTTP_URL]
	print(f"$ {' '.join(command)}")
	try:
		result = subprocess.run(
			command,
			capture_output=True,
			text=True,
			timeout=20,
		)
	except FileNotFoundError:
		return "HTTP 验证失败：未找到 curl，请先安装 curl"
	except subprocess.TimeoutExpired:
		return "HTTP 验证失败：curl 超时，请确认设备已启动"

	# 调试信息：curl -v 的连接过程、请求头与响应头（响应头为 "< " 前缀行）
	verbose = result.stderr.rstrip()
	if verbose:
		print(verbose)

	if result.returncode != 0:
		detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ""
		return (f"HTTP 验证失败：curl 退出码 {result.returncode}（{detail}）\n"
			f"请确认设备已启动，且已运行 test/config_web.py（设备重启后需重新运行）")

	status = "(无响应)"
	for line in result.stderr.splitlines():
		if line.startswith("< "):
			status = line[2:].strip()
			break
	body = result.stdout
	print(f"HTTP 响应：{status}")
	if body.strip():
		print("--- 响应正文 ---")
		print(body.rstrip())

	if "401" in status:
		return f"HTTP 验证失败：{status}（认证失败，请确认已运行 test/config_web.py）"
	if "200" not in status:
		return f"HTTP 验证失败：{status}"
	if "c2900 Home Page" not in body:
		return "HTTP 验证失败：响应中未找到 c2900 Home Page"
	return None


def dechunk(text):
	# 设备 HTTPS 响应为 chunked 编码，分块长度行会把正文切碎，这里去掉长度行便于阅读；
	# 响应不是 chunked 或解析失败时原样返回
	head, separator, body = text.partition("\r\n\r\n")
	if not separator or "chunked" not in head.lower():
		return text
	chunks = []
	rest = body
	while True:
		size_line, sep, rest = rest.partition("\r\n")
		if not sep:
			return text
		try:
			size = int(size_line.strip().split(";", 1)[0], 16)
		except ValueError:
			return text
		if size == 0:
			return head + separator + "".join(chunks)
		chunks.append(rest[:size])
		rest = rest[size:]
		if not rest.startswith("\r\n"):
			return text
		rest = rest[2:]


def check_https():
	try:
		from tlslite import TLSConnection, HandshakeSettings
	except ImportError:
		return "HTTPS 验证失败：缺少 tlslite-ng，请先运行 test/download_deps.sh 或 pip3 install tlslite-ng"

	# 设备 HTTPS 只支持 SSLv3 + RC4/DES/3DES 旧密码套件（ServerHello 版本 0300），
	# 现代 curl/openssl（OpenSSL 3 已移除这些套件）无法完成握手，必须用 tlslite-ng。
	settings = HandshakeSettings()
	settings.minVersion = (3, 0)
	settings.maxVersion = (3, 0)
	settings.cipherNames = ["rc4"]
	settings.keyExchangeNames = ["rsa"]
	settings.minKeySize = 512  # 设备使用 768 位临时密钥，默认 1024 下限会拒绝

	try:
		sock = socket.create_connection((HOST, HTTPS_PORT), timeout=20)
	except OSError as exc:
		return (f"HTTPS 验证失败：无法连接 {HOST}:{HTTPS_PORT}（{exc}）\n"
			f"请确认设备已启动，且已运行 test/config_web.py（设备重启后需重新运行）")

	connection = TLSConnection(sock)
	try:
		connection.handshakeClientCert(settings=settings)  # 自签名证书，跳过校验
	except Exception as exc:
		return f"HTTPS 验证失败：TLS 握手失败：{exc}"
	# 调试信息：TLS 版本与协商出的密码套件
	print(f"TLS 握手成功，cipher = 0x{connection.session.cipherSuite:x}")
	print(f"TLS 版本 = {connection.getVersionName()}，密码套件 = {connection.session.getCipherName()}")

	auth = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
	request = (f"GET / HTTP/1.1\r\n"
		   f"Host: {HOST}\r\n"
		   f"Authorization: Basic {auth}\r\n"
		   f"Connection: close\r\n\r\n")
	print("--- HTTPS 请求 ---")
	print(request.rstrip())
	connection.send(request.encode())

	response = b""
	try:
		while True:
			data = connection.recv(4096)
			if not data:
				break
			response += data
	except OSError:
		pass  # 超时或设备中途断开，用已收到的数据判定

	text = response.decode("utf-8", "replace")
	status = text.splitlines()[0] if text else "(无响应)"
	display = dechunk(text)
	print(f"HTTP 响应：{status}")
	if display.strip():
		print("--- 响应（响应头 + 正文） ---")
		print(display.rstrip())

	if "200" not in status:
		return f"HTTPS 验证失败：{status}"
	if "c2900 Home Page" not in display:
		return "HTTPS 验证失败：响应中未找到 c2900 Home Page"
	return None


def main():
	parser = argparse.ArgumentParser(description="验证 C2900 HTTP 与 HTTPS Web 服务")
	parser.add_argument("--http", action="store_true", help="只验证 HTTP")
	parser.add_argument("--https", action="store_true", help="只验证 HTTPS")
	args = parser.parse_args()

	only_one = args.http or args.https
	checks = []
	if args.http or not only_one:
		checks.append(("HTTP", check_http))
	if args.https or not only_one:
		checks.append(("HTTPS", check_https))

	failed = []
	for name, check in checks:
		error = check()
		if error:
			print(error, file=sys.stderr)
			failed.append(name)
		else:
			print(f"{name} 验证通过!")

	if failed:
		raise SystemExit(f"验证失败：{', '.join(failed)}")


if __name__ == "__main__":
	main()
