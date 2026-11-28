#!/bin/bash
# Read-only status for one event: window, active flag, participation counts.
# Usage: vps.py --run-script tools/check_event_status.sh EVENT_ID [EVENT_ID2 ...]
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
for eid in sys.argv[1:]:
    cur.execute("SELECT id, name, start_time, end_time, active FROM events WHERE id=%s", (eid,))
    e = cur.fetchone()
    if not e:
        print(f'{eid}: NOT FOUND')
        continue
    cur.execute("SELECT COUNT(DISTINCT username) FROM club_results WHERE event_id=%s", (eid,))
    drivers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM stage_runs WHERE event_id=%s", (eid,))
    runs = cur.fetchone()[0]
    print(f'{e[0]} "{e[1]}" active={e[4]} window={e[2]} -> {e[3]} drivers={drivers} runs={runs}')
EOF
