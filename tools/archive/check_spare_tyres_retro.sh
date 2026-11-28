#!/bin/bash
# Spare-tyres retro coverage check: how many historical stage slots carry a
# stored tuning blob (the board can decode the spare count from it for pre-v0.1.74
# runs), plus a sample decode of oldest -> newest events to prove old blobs
# parse with the current 222-byte/offset-210 layout.
# Usage: vps.py --run-script tools/check_spare_tyres_retro.sh
cd /opt/dr3/web
/opt/dr3/.venv/bin/python <<'EOF'
import base64, re, struct, zlib
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

def spare_from_b64(b64):
    try:
        data = base64.b64decode(b64)
        if len(data) < 16:
            return '<short>'
        hdr = struct.unpack_from('<IIII', data, 0)
        payload = zlib.decompress(data[16:])
        if len(payload) != 222:
            return f'<payload {len(payload)}B>'
        v = struct.unpack_from('<f', payload, 210)[0]
        return int(round(v))
    except Exception as e:
        return f'<err {e}>'

cur.execute("""
    SELECT COUNT(*), SUM(CASE WHEN s.tuning_setup_b64 IS NOT NULL AND s.tuning_setup_b64 != '' THEN 1 ELSE 0 END)
    FROM club_stage_states s""")
total, with_blob = cur.fetchone()
print(f'== coverage: {with_blob}/{total} stage slots carry a tuning blob ==')

cur.execute("""
    SELECT e.id, MIN(e.start_time), COUNT(*),
           SUM(CASE WHEN s.tuning_setup_b64 IS NOT NULL AND s.tuning_setup_b64 != '' THEN 1 ELSE 0 END)
    FROM club_stage_states s JOIN events e ON e.id = s.event_id
    GROUP BY e.id, e.start_time ORDER BY MIN(e.start_time)""")
rows = cur.fetchall()
print(f'== per event (oldest first): id, start, slots, with_blob ==')
for r in rows:
    print(r)

print('== sample decodes (first user, flat stage 0; oldest + newest events) ==')
picks = [rows[0][0], rows[-1][0]] if len(rows) >= 2 else [r[0] for r in rows]
for eid in picks:
    cur.execute("""SELECT username, tuning_setup_b64 FROM club_stage_states
                   WHERE event_id=%s AND flat_stage_index=0
                     AND tuning_setup_b64 IS NOT NULL AND tuning_setup_b64 != ''
                   ORDER BY id LIMIT 1""", (eid,))
    r = cur.fetchone()
    if r:
        print(eid, r[0], '-> spares:', spare_from_b64(r[1]))
    else:
        print(eid, '-> no blob at stage 0')
EOF
