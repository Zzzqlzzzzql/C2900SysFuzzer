#!/usr/bin/env python3

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

COMMANDS = (
	("c2900>", "enable"),
	("Password:", "cisco"),
	("c2900#", "configure terminal"),
	("c2900(config)#", "snmp-server community public RO"),
	("c2900(config)#", "snmp-server community private RW"),
	("c2900(config)#", "end"),
	("c2900#", "disable"),
)


def pane_text():
	return subprocess.run(
		["tmux", "capture-pane", "-p", "-t", TARGET],
		check=True,
		capture_output=True,
		text=True,
	).stdout


def wait_for_prompt(prompt):
	start = time.monotonic()
	deadline = start + PROMPT_TIMEOUT
	enter_sent = False
	while time.monotonic() < deadline:
		if pane_text().rstrip().endswith(prompt):
			return
		if not enter_sent and prompt.startswith("c2900") and time.monotonic() >= start + 1:
			subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)
			enter_sent = True
		time.sleep(0.1)
	raise SystemExit(f"Timed out waiting for {prompt}")


def send_command(command):
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "-l", command], check=True)
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)


if subprocess.run(["tmux", "has-session", "-t", "console"], capture_output=True).returncode:
	raise SystemExit("tmux session 'console' is not running")

for expected_prompt, command in COMMANDS:
	wait_for_prompt(expected_prompt)
	send_command(command)

wait_for_prompt("c2900>")
print("SNMP configuration complete")
