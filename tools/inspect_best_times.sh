#!/bin/bash
# Dump best_times state from the live dr3 DB: orphaned placeholder count
# (tyre_compound=0), total rows, newest rows, and optional per-track detail.
# Usage: python tools/vps.py --run-script tools/inspect_best_times.sh [track_model_id]
cd /opt/dr3/web
TRACK="${1:-}" /opt/dr3/.venv/bin/python - <<'EOF'
import os, sys
track_arg = os.environ.get('TRACK', '').strip() or None
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

track = track_arg

cur.execute("SELECT COUNT(*) FROM best_times")
print(f"total rows: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM best_times WHERE tyre_compound = 0")
orphans = cur.fetchone()[0]
print(f"compound=0 placeholders (orphan candidates): {orphans}")

cols = ("id, username, vehicle_id, track, conditions, stage_time_ms, "
        "tyre_compound, (ghost_data_b64 IS NOT NULL) AS has_ghost, "
        "(splits_json IS NOT NULL) AS has_splits")
if track:
    cur.execute(f"SELECT {cols} FROM best_times WHERE track = %s ORDER BY id", (track,))
else:
    cur.execute(f"SELECT {cols} FROM best_times ORDER BY id DESC LIMIT 12")
print(f"{'id':>5} {'user':<14} {'car':>4} {'trk':>4} {'cond':>4} {'ms':>8} "
      f"{'comp':>4} ghost splits")
for row in cur.fetchall():
    print(f"{row[0]:>5} {row[1]:<14} {row[2]:>4} {row[3]:>4} {row[4]:>4} "
          f"{row[5]:>8} {row[6]:>4} {'Y' if row[7] else '-':>5} "
          f"{'Y' if row[8] else '-':>6}")
conn.close()
EOF
