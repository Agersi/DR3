#!/bin/bash
# Audit: list every club stage_runs row missing flat_stage_index, grouped by
# event, so repair_flat_index.sh can be pointed at each affected event.
# Official/TT rows (no event_id) are excluded — they never carried a flat
# ordinal. Usage: audit_null_flat.sh
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - <<'EOF'
import re
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
cur.execute("""SELECT event_id, COUNT(*) AS n, MIN(received_at) AS first_rx, MAX(received_at) AS last_rx
    FROM stage_runs
    WHERE flat_stage_index IS NULL AND event_id IS NOT NULL
    GROUP BY event_id ORDER BY last_rx DESC""")
print('== events with NULL-flat club rows ==')
for eid, n, first_rx, last_rx in cur.fetchall():
    print(f'{eid}: {n} row(s)  {first_rx} .. {last_rx}')
cur.execute("""SELECT s.id, s.username, s.event_id, s.challenge_id, s.stage_index,
    s.track_model_id, s.time_ms, s.player_finished, s.received_at
    FROM stage_runs s
    WHERE s.flat_stage_index IS NULL AND s.event_id IS NOT NULL
    ORDER BY s.received_at DESC LIMIT 40""")
print('== rows (newest first) ==')
for r in cur.fetchall():
    print(r)
print('== done ==')
EOF
