#!/bin/bash
# Show the newest rows of best_times + stage_runs (and whether the best row's
# run_id points at a real, matching stage_runs row). Run via:
#   python tools\vps.py --run-script tools\db_inspect_last_runs.sh [N] [BEST_ID]
N=${1:-5}
BTID=${2:-}
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$N" "${BTID}" <<'EOF'
import sys
n = int(sys.argv[1] or 5)
btid = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
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

print(f'== best_times (last {n}) ==')
cur.execute("""SELECT id, username, vehicle_id, track, conditions, tyre_compound,
                      stage_time_ms, run_id, submitted_at
               FROM best_times ORDER BY submitted_at DESC, id DESC LIMIT %s""", (n,))
for row in cur.fetchall():
    print('bt  id=%s user=%s vid=%s track=%s cond=%s comp=%s time=%sms run_id=%s at=%s' % row)

print()
print(f'== stage_runs (last {n}) ==')
cur.execute("""SELECT id, username, source_type, challenge_id, event_index, stage_index,
                      track_model_id, vehicle_id, conditions_id, tyre_compound,
                      time_ms, player_finished, received_at
               FROM stage_runs ORDER BY received_at DESC, id DESC LIMIT %s""", (n,))
for row in cur.fetchall():
    print('sr  id=%s user=%s src=%s ch=%s ei=%s si=%s track=%s vid=%s cond=%s comp=%s time=%sms fin=%s at=%s' % row)

print()
print('== cross-check: best rows with NULL run_id (last 10 by submitted_at) ==')
cur.execute("""SELECT bt.id, bt.username, bt.vehicle_id, bt.track, bt.conditions,
                      bt.tyre_compound, bt.stage_time_ms, bt.submitted_at,
                      sr.id AS sr_id, sr.time_ms AS sr_time_ms, sr.received_at AS sr_at
               FROM best_times bt
               LEFT JOIN stage_runs sr ON sr.id = bt.run_id
               WHERE bt.run_id IS NULL
               ORDER BY bt.submitted_at DESC LIMIT 10""")
for row in cur.fetchall():
    print('bt  id=%s user=%s vid=%s track=%s cond=%s comp=%s time=%sms at=%s | run_id=NULL (no sr join)' % row[:8])

print()
print('== cross-check: best rows whose run_id points at a mismatched run (last 10) ==')
cur.execute("""SELECT bt.id, bt.username, bt.stage_time_ms, bt.run_id,
                      sr.id AS sr_id, sr.time_ms AS sr_time_ms,
                      sr.track_model_id, sr.vehicle_id, sr.conditions_id, sr.source_type
               FROM best_times bt
               JOIN stage_runs sr ON sr.id = bt.run_id
               WHERE NOT (sr.time_ms = bt.stage_time_ms
                          AND sr.track_model_id = CAST(bt.track AS UNSIGNED)
                          AND sr.vehicle_id = bt.vehicle_id)
               ORDER BY bt.submitted_at DESC LIMIT 10""")
for row in cur.fetchall():
    print('bt  id=%s user=%s time=%sms run_id=%s | sr id=%s time=%sms track=%s vid=%s cond=%s src=%s' % row)

if btid:
    print()
    print(f'== specific best_times id={btid} ==')
    cur.execute("""SELECT bt.id, bt.username, bt.vehicle_id, bt.track, bt.conditions,
                          bt.tyre_compound, bt.stage_time_ms, bt.run_id, bt.submitted_at,
                          sr.id AS sr_id, sr.time_ms AS sr_time_ms, sr.received_at AS sr_at
                   FROM best_times bt
                   LEFT JOIN stage_runs sr ON sr.id = bt.run_id
                   WHERE bt.id = %s""", (btid,))
    row = cur.fetchone()
    if not row:
        print('no such best_times id')
    else:
        print('bt  id=%s user=%s vid=%s track=%s cond=%s comp=%s time=%sms run_id=%s at=%s' % row[:9])
        print('    -> sr id=%s time=%sms received=%s' % row[9:])
EOF
