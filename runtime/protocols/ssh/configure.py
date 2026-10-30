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


def history_text():
	# 连同回滚缓冲，避免 show 输出被顶出可见区域
	return subprocess.run(
		["tmux", "capture-pane", "-p", "-S", "-", "-t", TARGET],
		check=True,
		capture_output=True,
		text=True,
	).stdout


def seen_prompt(prompt, depth=5):
	lines = [line.rstrip() for line in pane_text().splitlines() if line.strip()]
	return any(line.endswith(prompt) for line in lines[-depth:])


def wait_for_prompt(prompt, timeout=PROMPT_TIMEOUT):
	start = time.monotonic()
	deadline = start + timeout
	next_tap = start + 1
	while time.monotonic() < deadline:
		if pane_text().rstrip().endswith(prompt):
			return
		# 串口输出偶发被憋住；周期性补敲 Enter（rel）
		if prompt.startswith("c2900") and time.monotonic() >= next_tap:
			subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)
			next_tap = time.monotonic() + 3
		time.sleep(0.1)
	raise SystemExit(f"Timed out waiting for {prompt}")


def wait_for_text(options, timeout=TEXT_TIMEOUT):
	# 用回滚缓冲，避免提示被日志顶出可见区域后永远匹配不到
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		lines = history_text().splitlines()[-40:]
		for line in lines:
			low = line.lower()
			for option in options:
				if option in low:
					return option
		time.sleep(0.2)
	raise SystemExit(f"Timed out waiting for {' or '.join(options)}")


def send_command(command):
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "-l", command], check=True)
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)


if subprocess.run(["tmux", "has-session", "-t", "console"], capture_output=True).returncode:
	raise SystemExit("tmux session 'console' is not running")

# 启动配置可能把设备留在 config 模式或刚触发一次 SYS-5-RESTART；
# 先归一到用户模式，和 config_web.py 的做法一致。
time.sleep(10)
if seen_prompt(")#"):
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "C-z"], check=True)
	wait_for_prompt("c2900#")
if seen_prompt("c2900#"):
	send_command("disable")
	wait_for_prompt("c2900>")

now = datetime.datetime.now()
clock_command = f"clock set {now:%H:%M:%S} {MONTHS[now.month - 1]} {now.day} {now.year}"

SKIP_IFACE = _os.environ.get("C2900_SSH_SKIP_INTERFACE", "0") == "1"

base_commands = [
	("c2900>", "enable"),
	("Password:", "cisco"),
	("c2900#", clock_command),
	("c2900#", "configure terminal"),
	("c2900(config)#", "hostname c2900"),
	("c2900(config)#", "ip domain-name local.test"),
]

for expected_prompt, command in base_commands:
	wait_for_prompt(expected_prompt)
	send_command(command)

if not SKIP_IFACE:
	# 确保 Gi0/0 有可达地址（config_ssh.py 本身不配接口 IP；
	# 原 C2900 流程通常先跑 config_telnet.py 配 172.16.100.2）
	wait_for_prompt("c2900(config)#")
	send_command("interface GigabitEthernet0/0")
	try:
		wait_for_prompt("c2900(config-if)#", timeout=60)
	except SystemExit:
		raise SystemExit(
			"interface GigabitEthernet0/0 rejected: the current board did not "
			"expose IOS network interfaces. Use -M octeon with "
			"model=octeon-eth (v2 baseline), or C2900_SSH_SKIP_INTERFACE=1 "
			"for snapshot-only testing."
		)
	for expected_prompt, command in (
		("c2900(config-if)#", "ip address 172.16.100.2 255.255.255.0"),
		("c2900(config-if)#", "no shutdown"),
		("c2900(config-if)#", "exit"),
	):
		wait_for_prompt(expected_prompt)
		send_command(command)
else:
	print("skip Gi0/0 configuration (C2900_SSH_SKIP_INTERFACE=1; "
	      "current board has no IOS network interfaces)")

# C2900 包启动配置已经有 RSA key（banner 后可见 SSH-5-ENABLED），
# 生成新 key 很慢且会交互提示；默认跳过，需要时显式打开。
if _os.environ.get("C2900_SSH_GENERATE_RSA", "0") == "1":
	wait_for_prompt("c2900(config)#")
	send_command("crypto key generate rsa")
	rsa_prompt = wait_for_text(("yes/no", "modulus"))
	if rsa_prompt == "yes/no":
		send_command("yes")
		wait_for_text(("modulus",))
	send_command("1024")
	wait_for_prompt("c2900(config)#", timeout=RSA_TIMEOUT)
else:
	print("skip RSA key generation (C2900_SSH_GENERATE_RSA=0)")

for expected_prompt, command in (
	("c2900(config)#", "username admin secret cisco"),
	("c2900(config)#", "line vty 0 4"),
	("c2900(config-line)#", "transport input ssh"),
	("c2900(config-line)#", "login local"),
	("c2900(config-line)#", "end"),
):
	wait_for_prompt(expected_prompt)
	send_command(command)

wait_for_prompt("c2900#")

# 校验：show running-config 中出现 transport input ssh，且 Gi0/0 有 IP。
# 用发送前/后的出现次数差，避免把之前输入命令的回显误当成配置成功。
before_ssh = history_text().count("transport input ssh")
send_command("show running-config | include transport input")
time.sleep(4)
wait_for_prompt("c2900#")
after_ssh = history_text().count("transport input ssh")
if after_ssh <= before_ssh:
	raise SystemExit("SSH verification failed: transport input ssh not in running-config")

if not SKIP_IFACE:
	before_ip = history_text().count("172.16.100.2")
	send_command("show ip interface brief")
	time.sleep(4)
	wait_for_prompt("c2900#")
	after_ip = history_text().count("172.16.100.2")
	if after_ip <= before_ip:
		raise SystemExit("SSH verification failed: Gi0/0 172.16.100.2 not in show ip interface brief")

send_command("disable")
wait_for_prompt("c2900>")
if SKIP_IFACE:
	print("SSH configuration complete (verified VTY transport ssh; "
	      "interface setup skipped, no network fuzz path)")
else:
	print("SSH configuration complete (verified: Gi0/0 172.16.100.2/24, VTY transport ssh)")
