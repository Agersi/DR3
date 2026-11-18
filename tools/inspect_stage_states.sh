#!/bin/bash
# Read-only dump of club_stage_states rows — per-event/username counts + key list.
# Usage: vps.py --run-script tools/inspect_stage_states.sh [event_id]
#   (no arg = all rows, grouped by event)
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$@" <<'EOF'
import re, sys
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

eid = sys.argv[1] if len(sys.argv) > 1 else None
if eid:
    cur.execute("SELECT event_id, username, flat_stage_index, "
                "LENGTH(tuning_setup_b64), LENGTH(comp_damage_json), LENGTH(vehicle_mud_json) "
                "FROM club_stage_states WHERE event_id=%s ORDER BY username, flat_stage_index", (eid,))
    rows = cur.fetchall()
    print(f'== club_stage_states for {eid}: {len(rows)} row(s) ==')
    for r in rows:
        print(r)
else:
    cur.execute("SELECT event_id, COUNT(*) AS n, COUNT(DISTINCT username) AS drivers "
                "FROM club_stage_states GROUP BY event_id ORDER BY event_id")
    print('== club_stage_states by event ==')
    for r in cur.fetchall():
        print(r)
EOF
