#!/usr/bin/env python3

import datetime
import subprocess
import time


TARGET = "console:0.0"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def pane_text():
	return subprocess.run(
		["tmux", "capture-pane", "-p", "-t", TARGET],
		check=True,
		capture_output=True,
		text=True,
	).stdout


def wait_for_prompt(prompt, timeout=30):
	start = time.monotonic()
	deadline = start + timeout
	enter_sent = False
	while time.monotonic() < deadline:
		if pane_text().rstrip().endswith(prompt):
			return
		if not enter_sent and prompt.startswith("c2900") and time.monotonic() >= start + 1:
			subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)
			enter_sent = True
		time.sleep(0.1)
	raise SystemExit(f"Timed out waiting for {prompt}")


def wait_for_text(options, timeout=30):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		lines = pane_text().rstrip().splitlines()
		last_line = lines[-1].lower() if lines else ""
		for option in options:
			if option in last_line:
				return option
		time.sleep(0.1)
	raise SystemExit(f"Timed out waiting for {' or '.join(options)}")


def send_command(command):
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "-l", command], check=True)
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)


if subprocess.run(["tmux", "has-session", "-t", "console"], capture_output=True).returncode:
	raise SystemExit("tmux session 'console' is not running")

now = datetime.datetime.now()
clock_command = f"clock set {now:%H:%M:%S} {MONTHS[now.month - 1]} {now.day} {now.year}"

for expected_prompt, command in (
	("c2900>", "enable"),
	("Password:", "cisco"),
	("c2900#", clock_command),
	("c2900#", "configure terminal"),
	("c2900(config)#", "hostname c2900"),
	("c2900(config)#", "ip domain-name local.test"),
	("c2900(config)#", "crypto key generate rsa"),
):
	wait_for_prompt(expected_prompt)
	send_command(command)

rsa_prompt = wait_for_text(("yes/no", "modulus"))
if rsa_prompt == "yes/no":
	send_command("yes")
	wait_for_text(("modulus",))
send_command("1024")
wait_for_prompt("c2900(config)#", timeout=60)

for expected_prompt, command in (
	("c2900(config)#", "username admin secret cisco"),
	("c2900(config)#", "line vty 0 4"),
	("c2900(config-line)#", "transport input ssh"),
	("c2900(config-line)#", "login local"),
	("c2900(config-line)#", "end"),
	("c2900#", "disable"),
):
	wait_for_prompt(expected_prompt)
	send_command(command)

wait_for_prompt("c2900>")
print("SSH configuration complete")
