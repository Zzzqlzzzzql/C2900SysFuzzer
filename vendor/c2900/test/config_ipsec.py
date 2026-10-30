#!/usr/bin/env python3

import subprocess
import time


TARGET = "console:0.0"
IPSEC_CONF = """config setup
    uniqueids=no
conn c2900
    type=tunnel
    keyexchange=ikev1
    left=172.16.100.1
    leftsubnet=172.16.100.1/32[icmp]
    right=172.16.100.2
    rightsubnet=172.16.100.2/32[icmp]
    ike=3des-sha1-modp1536!
    esp=3des-sha1!
    authby=psk
    keyingtries=%forever
    auto=route
"""
IPSEC_SECRETS = '172.16.100.1 172.16.100.2 : PSK "cisco123"\n'


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


def send_command(command):
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "-l", command], check=True)
	subprocess.run(["tmux", "send-keys", "-t", TARGET, "Enter"], check=True)


def run_ipsec_up():
	result = subprocess.run(
		["sudo", "ipsec", "up", "c2900"],
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
	)
	print(result.stdout, end="")
	return result


def ipsec_established():
	result = subprocess.run(
		["sudo", "ipsec", "status", "c2900"],
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
	)
	return "ESTABLISHED" in result.stdout


def wait_for_ipsec_config():
	deadline = time.monotonic() + 30
	while time.monotonic() < deadline:
		result = subprocess.run(
			["sudo", "ipsec", "status", "c2900"],
			stdout=subprocess.PIPE,
			stderr=subprocess.STDOUT,
			text=True,
		)
		if "c2900" in result.stdout:
			return
		time.sleep(0.2)
	raise SystemExit("Timed out waiting for strongSwan to load connection 'c2900'")


def configure_host():
	subprocess.run(["sudo", "-v"], check=True)
	subprocess.run(["sudo", "tee", "/etc/ipsec.conf"], input=IPSEC_CONF, text=True, stdout=subprocess.DEVNULL, check=True)
	subprocess.run(
		["sudo", "tee", "/etc/ipsec.secrets"],
		input=IPSEC_SECRETS,
		text=True,
		stdout=subprocess.DEVNULL,
		check=True,
	)
	subprocess.run(["sudo", "chmod", "600", "/etc/ipsec.secrets"], check=True)
	subprocess.run(["sudo", "ipsec", "start"], check=False)

	result = run_ipsec_up()
	if "no config named 'c2900'" in result.stdout.lower():
		subprocess.run(["sudo", "ipsec", "restart"], check=True)
		wait_for_ipsec_config()
		result = run_ipsec_up()
	if "establishing connection 'c2900' failed" in result.stdout.lower():
		time.sleep(2)
		result = run_ipsec_up()
	if not ipsec_established():
		raise SystemExit("Failed to establish IPsec connection 'c2900'")


if subprocess.run(["tmux", "has-session", "-t", "console"], capture_output=True).returncode:
	raise SystemExit("tmux session 'console' is not running")

for expected_prompt, command in (
	("c2900>", "enable"),
	("Password:", "cisco"),
	("c2900#", "configure terminal"),
	("c2900(config)#", "crypto isakmp policy 10"),
	("c2900(config-isakmp)#", "encr 3des"),
	("c2900(config-isakmp)#", "hash sha"),
	("c2900(config-isakmp)#", "authentication pre-share"),
	("c2900(config-isakmp)#", "group 5"),
	("c2900(config-isakmp)#", "exit"),
	("c2900(config)#", "crypto isakmp key cisco123 address 172.16.100.1"),
	("c2900(config)#", "crypto ipsec transform-set TSET esp-3des esp-sha-hmac"),
	("c2900(cfg-crypto-trans)#", "access-list 100 permit icmp host 172.16.100.2 host 172.16.100.1"),
	("c2900(config)#", "crypto map CMAP 10 ipsec-isakmp"),
	("c2900(config-crypto-map)#", "set peer 172.16.100.1"),
	("c2900(config-crypto-map)#", "set transform-set TSET"),
	("c2900(config-crypto-map)#", "match address 100"),
	("c2900(config-crypto-map)#", "exit"),
	("c2900(config)#", "interface GigabitEthernet0/0"),
	("c2900(config-if)#", "crypto map CMAP"),
	("c2900(config-if)#", "end"),
):
	wait_for_prompt(expected_prompt)
	send_command(command)

wait_for_prompt("c2900#")
time.sleep(2)
try:
	configure_host()
finally:
	wait_for_prompt("c2900#")
	send_command("disable")
	wait_for_prompt("c2900>")

print("IPsec configuration complete")
