#!/usr/bin/env python3
"""SSH readiness probe for the emulated C2900.

C2900/IOS uses old algorithms; this probe forces the same set as the real
client used in the 2026-10-29 verification.  It performs a real login
(auth_password) and closes the session, so a successful probe means the
VTY/SSH path is actually usable, not just that TCP/22 is open.
"""
import argparse
import socket
import sys
import time

import paramiko


def one_attempt(args):
    raw = socket.create_connection((args.host, args.port), timeout=args.timeout)
    raw.settimeout(args.timeout)
    t = paramiko.Transport(raw)
    try:
        o = t.get_security_options()
        o.kex = ("diffie-hellman-group14-sha1",)
        o.ciphers = ("aes128-cbc",)
        o.digests = ("hmac-sha1",)
        o.key_types = ("ssh-rsa",)
        t.start_client(timeout=args.timeout)
        banner = t.remote_version
        t.auth_password(args.user, args.password)
        # Exercise the session a little, then close; this is not a fuzz round.
        ch = t.open_session(timeout=args.timeout)
        ch.close()
        return True, banner
    finally:
        t.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="172.16.100.2")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="cisco")
    ap.add_argument("--timeout", type=float, default=8.0,
                    help="per-attempt socket/ssh timeout")
    ap.add_argument("--wait", type=float, default=0.0,
                    help="overall retry window in seconds (0 = one attempt)")
    args = ap.parse_args()

    deadline = time.time() + args.wait
    last = ""
    while True:
        try:
            ok, banner = one_attempt(args)
            if ok:
                print(f"READY banner={banner}")
                return 0
        except Exception as exc:  # noqa: BLE001 - readiness probe
            last = f"{type(exc).__name__}: {exc}"
        if time.time() >= deadline:
            print(f"NOT_READY {last}", file=sys.stderr)
            return 1
        time.sleep(3)


if __name__ == "__main__":
    sys.exit(main())
