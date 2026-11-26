#!/bin/bash
# List the newest stage entries carrying a terminal race_status (4/5) across all
# club results, with their submitted_at values (the home-page "days without a
# terminal damage" stat only counts entries that HAVE one). Run via:
#   python tools\vps.py --run-script tools\db_inspect_terminal_damage.sh [N] [USERNAME]
N=${1:-15}
USERF=${2:-}
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$N" "${USERF}" <<'EOF'
import json, sys
n = int(sys.argv[1] or 15)
userf = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
env = {}
for line in open('.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k] = v
import re
m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)', env['DATABASE_URL'])
user, pw, host, port, dbname = m.group(1), m.group(2), m.group(3), int(m.group(4) or 3306), m.group(5).split('?')[0]
import pymysql
conn = pymysql.connect(host=host, port=port, user=user, password=pw, database=dbname)
cur = conn.cursor()

rows = []
q = "SELECT id, username, event_id, stages_json FROM club_results"
if userf:
    q += " WHERE username = %s"
cur.execute(q, (userf,) if userf else ())
for rid, uname, eid, sj in cur.fetchall():
    for i, s in enumerate(json.loads(sj or '[]')):
        rs = int(s.get('race_status', 0) or 0)
        if rs not in (4, 5):
            continue
        rows.append((s.get('submitted_at'), uname, eid, i, rs, s.get('time_ms'), rid))

rows.sort(key=lambda r: (r[0] or ''), reverse=True)
print(f'== terminal entries (race_status 4/5), newest submitted_at first (top {n}) ==')
for sa, uname, eid, i, rs, tm, rid in rows[:n]:
    print(f'rs={rs} at={sa!s:<27} user={uname:<20} evt={eid} flat={i} time={tm}ms result={rid}')

print()
print('== summary ==')
with_ts = [r for r in rows if r[0]]
without_ts = [r for r in rows if not r[0]]
print(f'total terminal entries: {len(rows)}  with submitted_at: {len(with_ts)}  WITHOUT: {len(without_ts)}')
if without_ts:
    print('newest WITHOUT submitted_at:')
    for sa, uname, eid, i, rs, tm, rid in sorted(without_ts, key=lambda r: (r[5] or 0), reverse=True)[:5]:
        print(f'  rs={rs} user={uname:<20} evt={eid} flat={i} time={tm}ms result={rid}')
EOF
