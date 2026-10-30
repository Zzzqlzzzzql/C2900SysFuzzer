#!/usr/bin/env python3

import subprocess
import time


TARGET = "console:0.0"


def pane_text():
	return subprocess.run(
		["tmux", "capture-pane", "-p", "-S", "-", "-t", TARGET],
		check=True,
		capture_output=True,
		text=True,
	).stdout


def wait_for_prompt(prompt, timeout=30):
	start = time.monotonic()
	deadline = start + timeout
	enter_sent = False
	while time.monotonic() < deadline:
		lines = pane_text().splitlines()
		if any(line.strip() == prompt for line in lines[-10:]):
			return
		if not enter_sent and prompt.startswith("c2900") and time.monotonic() >= start + 1:
			subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)
			enter_sent = True
		time.sleep(0.1)
	raise SystemExit(f"Timed out waiting for {prompt}")


def wait_for_license_prompt():
	deadline = time.monotonic() + 30
	while time.monotonic() < deadline:
		text = pane_text().rstrip()
		if text.endswith("[yes/no]:"):
			return True
		if text.endswith("c2900(config)#"):
			return False
		time.sleep(0.1)
	raise SystemExit("Timed out waiting for license confirmation")


def wait_for_ok(previous_count):
	deadline = time.monotonic() + 30
	while time.monotonic() < deadline:
		if pane_text().count("[OK]") > previous_count:
			return
		time.sleep(0.1)
	raise SystemExit("Timed out waiting for [OK]")


def send_command(command):
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "-l", command], check=True)
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)


if subprocess.run(["tmux", "has-session", "-t", "console"], capture_output=True).returncode:
	raise SystemExit("tmux session 'console' is not running")

for expected_prompt, command in (
	("c2900>", "enable"),
	("Password:", "cisco"),
	("c2900#", "configure terminal"),
	("c2900(config)#", "license boot module c2900 technology-package securityk9"),
):
	wait_for_prompt(expected_prompt)
	send_command(command)

if wait_for_license_prompt():
	send_command("yes")
wait_for_prompt("c2900(config)#")
send_command("end")

wait_for_prompt("c2900#")
ok_count = pane_text().count("[OK]")
send_command("write memory")
wait_for_ok(ok_count)
wait_for_prompt("c2900#")
send_command("disable")
wait_for_prompt("c2900>")
print("IPsec license configuration complete")
