#!/bin/bash
# Full decompressed TuningSetup payload hex dump + pairwise byte diffs per
# stage, for one or more event ids (default: the two 2026-10-26 multi events).
# Output is parseable by tools\tuning_field_analysis.py. Run via:
#   python tools\vps.py --run-script tools\db_dump_tuning_hex.sh
#   python tools\vps.py --run-script tools\db_dump_tuning_hex.sh evt-xxxx [evt-yyyy ...]
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$@" <<'EOF'
import base64, json, re, sys, zlib
env = {}
for line in open('.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k] = v
m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)', env['DATABASE_URL'])
user, pw, host, port, dbname = m.group(1), m.group(2), m.group(3), int(m.group(4) or 3306), m.group(5).split('?')[0]
import pymysql
conn = pymysql.connect(host=host, port=port, user=user, password=pw, database=dbname)
cur = conn.cursor()

def payload(b64):
    if not b64:
        return None
    raw = base64.b64decode(b64)
    ver, hs, usize, res = (int.from_bytes(raw[i:i+4], 'little') for i in (0, 4, 8, 12))
    body = raw[16:]
    try:
        return zlib.decompress(body)
    except Exception as e:
        return f'ZLIB-FAIL({e}) ver={ver} usize={usize} rawlen={len(raw)}'

if len(sys.argv) > 1:
    eids = sys.argv[1:]
else:
    eids = ['evt-d8e9697b', 'evt-7a60c4d4']

for eid in eids:
    cur.execute("SELECT name FROM events WHERE id=%s", (eid,))
    row = cur.fetchone()
    label = row[0] if row else eid
    cur.execute("SELECT stages_json FROM results WHERE event_id=%s LIMIT 1", (eid,))
    row = cur.fetchone()
    if not row:
        print(f'{label}: no result'); continue
    st = json.loads(row[0] or '[]')
    payloads = []
    for i, s in enumerate(st):
        pl = payload((s or {}).get('tuning_setup_b64', ''))
        if isinstance(pl, bytes):
            payloads.append(pl)
            print(f'{label} stage[{i}] usize={len(pl)}: {pl.hex()}')
        else:
            payloads.append(None)
            print(f'{label} stage[{i}]: {pl}')
    for a in range(len(payloads)):
        for b in range(a + 1, len(payloads)):
            pa, pb = payloads[a], payloads[b]
            if not isinstance(pa, bytes) or not isinstance(pb, bytes):
                continue
            n = min(len(pa), len(pb))
            first_diff = next((j for j in range(n) if pa[j] != pb[j]), None)
            ndiff = sum(1 for j in range(n) if pa[j] != pb[j])
            print(f'{label} diff stage[{a}] vs stage[{b}]: lens {len(pa)}/{len(pb)} first_diff_byte={first_diff} n_diff={ndiff}')
    print()
EOF
