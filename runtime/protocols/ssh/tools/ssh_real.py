#!/usr/bin/env python3
"""Real SSH client test against the emulated C2900 (old Cisco algorithms)."""
import socket, sys, time, argparse
import paramiko

ap=argparse.ArgumentParser()
ap.add_argument('--host', default='172.16.100.2')
ap.add_argument('--port', type=int, default=22)
ap.add_argument('--user', default='admin')
ap.add_argument('--password', default='cisco')
ap.add_argument('--command', default='show clock')
ap.add_argument('--timeout', type=float, default=20)
args=ap.parse_args()
log=open(sys.argv[0]+'.log','a',buffering=1)
def note(x):
    print(x); log.write(x+'\n')
note(f'=== {time.strftime("%F %T")} connect {args.host}:{args.port} ===')
raw=socket.create_connection((args.host,args.port),timeout=args.timeout)
raw.settimeout(args.timeout)
t=paramiko.Transport(raw)
o=t.get_security_options()
for name, val in (('kex',('diffie-hellman-group14-sha1',)),
                  ('ciphers',('aes128-cbc',)),
                  ('digests',('hmac-sha1',)),
                  ('key_types',('ssh-rsa',))):
    try:
        setattr(o,name,val); note(f'set {name}={val}')
    except Exception as e:
        note(f'set {name} failed: {e}')
t.start_client(timeout=args.timeout)
note(f'server banner: {t.remote_version}')
note(f'host key: {t.get_remote_server_key().get_name()} {t.get_remote_server_key().get_bits()}')
t.auth_password(args.user,args.password)
note('auth_password OK')
ch=t.open_session(timeout=args.timeout)
ch.settimeout(args.timeout)
ch.get_pty()
ch.invoke_shell()
time.sleep(1)
for cmd in ('terminal length 0', args.command, 'exit'):
    note(f'>>> {cmd}')
    ch.send(cmd+'\r')
    time.sleep(2)
    out=b''
    while ch.recv_ready():
        out+=ch.recv(4096)
    note(out.decode('latin1','replace'))
t.close()
note('done')
