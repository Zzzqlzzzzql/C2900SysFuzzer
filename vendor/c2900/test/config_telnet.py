#!/usr/bin/env python3

import subprocess
import time


TARGET = "console:0.0"
COMMANDS = (
	("c2900>", "enable"),
	("Password:", "cisco"),
	("c2900#", "configure terminal"),
	("c2900(config)#", "interface GigabitEthernet0/0"),
	("c2900(config-if)#", "ip address 172.16.100.2 255.255.255.0"),
	("c2900(config-if)#", "no shutdown"),
	("c2900(config-if)#", "exit"),
	("c2900(config)#", "enable password cisco"),
	("c2900(config)#", "line vty 0 4"),
	("c2900(config-line)#", "password cisco"),
	("c2900(config-line)#", "login"),
	("c2900(config-line)#", "transport input telnet"),
	("c2900(config-line)#", "end"),
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
	deadline = start + 30
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
print("Telnet configuration complete")
