#!/bin/bash
# Dump every result row (username, event, per-stage entries with times) for a
# list of event ids. Usage: inspect_results_full.sh EVENT_ID [EVENT_ID ...]
# Permanent tool for stage-flow investigation (backlog #32).
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
user, pw, host, port, dbname = m.group(1), m.group(2), m.group(3), int(m.group(4) or 3306), m.group(5).split('?')[0]
import pymysql
conn = pymysql.connect(host=host, port=port, user=user, password=pw, database=dbname)
cur = conn.cursor()
ids = sys.argv[1:] or None
if ids:
    id_list = ",".join("'" + i.replace("'", "") + "'" for i in ids)
    cur.execute(f"SELECT id, event_id, username, car, vehicle_id, attempts_used, total_time_ms, stages_json FROM results WHERE event_id IN ({id_list})")
else:
    cur.execute("SELECT id, event_id, username, car, vehicle_id, attempts_used, total_time_ms, stages_json FROM results")
for rid, event_id, username, car, vehicle_id, attempts, total_ms, stages_json in cur.fetchall():
    stages = json.loads(stages_json or '[]')
    print(f'--- result {rid} user={username} event={event_id} car={car} vid={vehicle_id} '
          f'attempts={attempts} total_ms={total_ms}')
    for p, s in enumerate(stages):
        print(f"    pos={p} time_ms={s.get('time_ms')} race_status={s.get('race_status')} "
              f"submitted_at={s.get('submitted_at')} vehicle_id={s.get('vehicle_id')} "
              f"meters={s.get('meters_driven')}")
EOF
