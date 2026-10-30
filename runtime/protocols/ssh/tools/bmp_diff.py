#!/usr/bin/env python3
import sys
a=open(sys.argv[1],'rb').read(); b=open(sys.argv[2],'rb').read()
sa={i for i,v in enumerate(a) if v}; sb={i for i,v in enumerate(b) if v}
print(f'{sys.argv[1]}: {len(sa)} edges; {sys.argv[2]}: {len(sb)} edges')
print(f'only in A: {len(sa-sb)}; only in B: {len(sb-sa)}; common: {len(sa&sb)}')
