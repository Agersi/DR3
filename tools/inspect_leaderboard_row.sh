#!/bin/bash
# Dump the leaderboard data path for one stage row: best_times rows for a
# track + vehicle plus the user's recent stage_runs on that track.
# Usage: inspect_leaderboard_row.sh [TRACK_MODEL_ID] [VEHICLE_ID] [USERNAME]
cd /opt/dr3/web
TRACK="${1:-583}"
VEHICLE="${2:-532}"
USER="${3:-Agersi}"
/opt/dr3/.venv/bin/python - <<EOF
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

def dump(sql, args=None):
    cur.execute(sql, args or ())
    cols = [d[0] for d in cur.description]
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        for k in list(d):
            if isinstance(d[k], (bytes, str)) and len(str(d[k])) > 80:
                d[k] = str(d[k])[:60] + f'...<{len(str(d[k]))} chars>'
        print(d)

print(f'== best_times track=$TRACK (all vehicles, latest 20) ==')
dump("SELECT * FROM best_times WHERE track=%s ORDER BY submitted_at DESC LIMIT 20", ('$TRACK',))

print(f'== stage_runs user=$USER track_model_id=$TRACK (latest 15) ==')
dump("""SELECT * FROM stage_runs
               WHERE username=%s AND track_model_id=%s
               ORDER BY received_at DESC LIMIT 15""", ('$USER', '$TRACK'))

print(f'== stage_runs user=$USER source_type=timetrial (latest 10) ==')
dump("""SELECT * FROM stage_runs
               WHERE username=%s AND source_type='timetrial'
               ORDER BY received_at DESC LIMIT 10""", ('$USER',))
EOF
