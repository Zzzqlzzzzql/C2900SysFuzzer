#!/usr/bin/env python3

import datetime
import subprocess
import time


TARGET = "console:0.0"

# --- C2900SysFuzzer protocol adaptation -------------------------------------
# 这些超时在原 vendor 脚本中是 30/60/120s；插桩后的 QEMU 很慢，
# 命令（尤其 RSA/HTTPS 证书生成）可能超过 2 分钟。这里允许用环境变量覆盖。
import os as _os
PROMPT_TIMEOUT = int(_os.environ.get("C2900_CONFIG_PROMPT_TIMEOUT", "300"))
TEXT_TIMEOUT = int(_os.environ.get("C2900_CONFIG_TEXT_TIMEOUT", "600"))
RSA_TIMEOUT = int(_os.environ.get("C2900_CONFIG_RSA_TIMEOUT", "900"))
# ---------------------------------------------------------------------------

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def pane_text():
	return subprocess.run(
		["tmux", "capture-pane", "-p", "-t", TARGET],
		check=True,
		capture_output=True,
		text=True,
	).stdout


def seen_prompt(prompt, depth=5):
	# 日志消息可能异步落在提示符之后，因此在最后几行里找提示符
	lines = [line.rstrip() for line in pane_text().splitlines() if line.strip()]
	return any(line.endswith(prompt) for line in lines[-depth:])


def recent_text(lines=40):
	return subprocess.run(
		["tmux", "capture-pane", "-p", "-S", f"-{lines}", "-t", TARGET],
		check=True,
		capture_output=True,
		text=True,
	).stdout


def history_lines():
	# 连同回滚缓冲一起抓取（-S -）：长输出（如 show ip http server status
	# 有 20 多行）会把命令回显顶出可见区域，只看可见区域会永远找不到回显
	text = subprocess.run(
		["tmux", "capture-pane", "-p", "-S", "-", "-t", TARGET],
		check=True,
		capture_output=True,
		text=True,
	).stdout
	return [line.rstrip() for line in text.splitlines() if line.strip()]


def echo_count(command):
	return sum(1 for line in history_lines() if command in line)


def text_after(marker):
	text = recent_text()
	if marker not in text:
		return ""
	return text.rsplit(marker, 1)[-1]


def wait_for_prompt(prompt, timeout=PROMPT_TIMEOUT, after=None, baseline=0):
	# after：要求提示符出现在该文本（命令回显）之后，避免读到上一条命令残留的提示符；
	# 回显在回滚缓冲里查找，不看可见区域；baseline 为发送前的回显出现次数，
	# 只有出现更多次才说明是本次命令的回显，而不是上一次运行残留在回滚缓冲里的
	start = time.monotonic()
	deadline = start + timeout
	next_tap = start + 1
	while time.monotonic() < deadline:
		if after is None:
			lines = [line.rstrip() for line in pane_text().splitlines() if line.strip()]
			if any(line.endswith(prompt) for line in lines):
				return
		else:
			lines = history_lines()
			echoes = [index for index, line in enumerate(lines) if after in line]
			if len(echoes) > baseline and any(
				line.endswith(prompt) for line in lines[echoes[-1] + 1:]
			):
				return
		# 串口输出偶发要收到一次输入才刷新（提示符连同延迟日志被憋住），
		# 单次补敲可能落在输出憋住之前被吞掉，因此周期性补敲
		if prompt.startswith("c2900") and time.monotonic() >= next_tap:
			subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)
			next_tap = time.monotonic() + 3
		time.sleep(0.1)
	raise SystemExit(f"Timed out waiting for {prompt}")


def wait_for_text(options, timeout=TEXT_TIMEOUT):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		lines = [line.lower() for line in pane_text().splitlines() if line.strip()][-5:]
		for option in options:
			if any(option in line for line in lines):
				return option
		time.sleep(0.1)
	raise SystemExit(f"Timed out waiting for {' or '.join(options)}")


def send_command(command):
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "-l", command], check=True)
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)


def send_and_wait(command, prompt, timeout=PROMPT_TIMEOUT):
	# 发送前先记录该命令的回显出现次数，等待时只有超过它才算本次命令的回显
	baseline = echo_count(command)
	send_command(command)
	wait_for_prompt(prompt, timeout=timeout, after=command, baseline=baseline)


def run_commands(commands):
	for expected_prompt, command in commands:
		wait_for_prompt(expected_prompt)
		send_command(command)


if subprocess.run(["tmux", "has-session", "-t", "console"], capture_output=True).returncode:
	raise SystemExit("tmux session 'console' is not running")

# 归一化到用户模式（支持在特权/配置模式重跑）
if seen_prompt(")#"):
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "C-z"], check=True)
	wait_for_prompt("c2900#")
if seen_prompt("c2900#"):
	send_command("disable")
	wait_for_prompt("c2900>")

# 设备 RTC 年份比宿主机少 100 年，且时钟无效时无法生成 RSA 密钥/证书，
# 因此必须在启用 HTTPS 前校准设备时钟（设备 clock 为 UTC）
now = datetime.datetime.now(datetime.timezone.utc)
clock_command = f"clock set {now:%H:%M:%S} {MONTHS[now.month - 1]} {now.day} {now.year}"

run_commands((
	("c2900>", "enable"),
	("Password:", "cisco"),
	("c2900#", "terminal length 0"),
	("c2900#", clock_command),
	("c2900#", "configure terminal"),
	("c2900(config)#", "hostname c2900"),
	("c2900(config)#", "ip domain-name local.test"),
	("c2900(config)#", "crypto key generate rsa"),
))

rsa_prompt = wait_for_text(("yes/no", "modulus"))
if rsa_prompt == "yes/no":
	send_command("no")  # 已有密钥，保留
else:
	send_command("1024")
wait_for_prompt("c2900(config)#", timeout=RSA_TIMEOUT)

run_commands((
	("c2900(config)#", "ip http server"),
	("c2900(config)#", "ip http secure-server"),
	("c2900(config)#", "ip http authentication local"),
	("c2900(config)#", "username admin privilege 15 secret cisco"),
	("c2900(config)#", "end"),
))
wait_for_prompt("c2900#")

# 校验：时钟已按预期年份生效，HTTP 与 HTTPS 服务均处于 Enabled
send_and_wait("show clock", "c2900#")
if str(now.year) not in text_after("show clock"):
	raise SystemExit("Clock verification failed")

send_and_wait("show ip http server status", "c2900#")
if "HTTP server status: Enabled" not in text_after("show ip http server status"):
	raise SystemExit("HTTP verification failed: http server not enabled")

send_and_wait("show ip http server secure status", "c2900#")
if "HTTP secure server status: Enabled" not in text_after("show ip http server secure status"):
	raise SystemExit("HTTPS verification failed: secure server not enabled")

send_command("disable")
wait_for_prompt("c2900>")
print("Web configuration complete (HTTP + HTTPS, clock set, both servers Enabled)")
