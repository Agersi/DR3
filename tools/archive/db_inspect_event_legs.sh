#!/bin/bash
# Dump the raw legs_json layout for one or more event ids (default: every event
# with result rows). Read-only. Used to map flat stage indices to locations/legs
# when analysing tyre carry-over across a multi-location championship.
# Run via:
#   python tools/vps.py --run-script tools/db_inspect_event_legs.sh [EVENT_ID ...]
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$@" <<'EOF'
import json, re, sys
env = {}
for line in open('.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k] = v
m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)', env['DATABASE_URL'])
import pymysql
conn = pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306), user=m.group(1), password=m.group(2), database=m.group(5).split('?')[0])
cur = conn.cursor()

if len(sys.argv) > 1:
    eids = sys.argv[1:]
else:
    cur.execute("SELECT DISTINCT event_id FROM club_results ORDER BY event_id")
    eids = [r[0] for r in cur.fetchall()]

for eid in eids:
    cur.execute("SELECT id, name, legs_json, settings_json FROM events WHERE id=%s", (eid,))
    row = cur.fetchone()
    if not row:
        print(f"===== {eid}: NOT FOUND =====")
        continue
    _, name, legs_json, settings_json = row
    settings = json.loads(settings_json or '{}')
    mode = settings.get('mode', '?')
    legs = json.loads(legs_json or '[]')
    print(f'===== {eid} | {name} | mode={mode} | legs={len(legs)} =====')
    for i, leg in enumerate(legs):
        print(f'  leg[{i}]: {json.dumps(leg)}')
    print()
EOF
