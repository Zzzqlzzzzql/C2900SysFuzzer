#!/usr/bin/env python3
"""Send real SSH protocol bytes (version line + KEXINIT) over TCP, log response."""
import socket, sys, time, argparse, binascii
ap=argparse.ArgumentParser()
ap.add_argument('--host', default='172.16.100.2')
ap.add_argument('--port', type=int, default=22)
ap.add_argument('--seed', default='runtime/protocols/ssh/seeds/ssh_kexinit.raw')
ap.add_argument('--read', type=float, default=5)
ap.add_argument('--tag', default='kex')
args=ap.parse_args()
data=open(args.seed,'rb').read()
s=socket.create_connection((args.host,args.port),timeout=10); s.settimeout(args.read)
t0=time.time()
banner=b''
try:
    while b'\n' not in banner and time.time()-t0<5:
        banner+=s.recv(1024)
except socket.timeout: pass
print(f'[{args.tag}] server banner: {banner!r}')
s.sendall(data)
print(f'[{args.tag}] sent {len(data)} bytes ({args.seed})')
out=b''
end=time.time()+args.read
while time.time()<end:
    try:
        d=s.recv(65536)
        if not d: break
        out+=d
    except socket.timeout: break
print(f'[{args.tag}] got {len(out)} bytes')
print(binascii.hexlify(out[:512]).decode())
s.close()
