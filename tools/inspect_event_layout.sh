#!/bin/bash
# Dump a club event's championship layout (per-leg stage counts + flat offsets)
# and its stage_runs rows. Usage: inspect_event_layout.sh EVENT_ID [FILTER]
# FILTER: "dnf" (player_finished=0), "flatnull", or empty (all).
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$@" <<'EOF'
import sys, re, json
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

event_id = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] not in ('', '-') else None
flt = sys.argv[2] if len(sys.argv) > 2 else ''
if not event_id:
    print('usage: inspect_event_layout.sh EVENT_ID [dnf|flatnull]')
    sys.exit(1)

cur.execute("SELECT id, name, club_id, schema_version, legs_json FROM events WHERE id=%s", (event_id,))
row = cur.fetchone()
if not row:
    print(f'event {event_id} not found')
    sys.exit(1)
eid, name, club_id, ver, legs_json = row
print(f'== event {eid} "{name}" club={club_id} schema_version={ver} ==')
try:
    legs = json.loads(legs_json) if legs_json else []
except ValueError:
    legs = []
offset = 0
for i, leg in enumerate(legs or []):
    stages = leg.get('stages') or []
    tids = [s.get('track_id') for s in stages]
    print(f'leg {i}: {len(stages)} stages flat {offset}..{offset + len(stages) - 1} '
          f'tracks={tids} location={leg.get("location")}')
    offset += len(stages)

where = f"event_id='{event_id}'"
if flt == 'dnf':
    where += " AND player_finished=0"
elif flt == 'flatnull':
    where += " AND flat_stage_index IS NULL"
cur.execute(f"""SELECT id, username, challenge_id, event_index, stage_index,
    track_model_id, vehicle_id, time_ms, player_finished, player_end_status,
    flat_stage_index, input_method, camera_position, tyre_compound,
    LENGTH(splits_json) AS splits_len, LENGTH(comp_damage_json) AS dmg_len, received_at
    FROM stage_runs WHERE {where} ORDER BY received_at DESC LIMIT 60""")
cols = [d[0] for d in cur.description]
n = 0
for r in cur.fetchall():
    print(dict(zip(cols, r)))
    n += 1
print(f'== {n} stage_runs rows ==')

cur.execute("""SELECT username, stages_json FROM club_results WHERE event_id=%s""", (event_id,))
for uname, sj in cur.fetchall():
    try:
        stages = json.loads(sj) if sj else []
    except ValueError:
        stages = []
    for i, s in enumerate(stages or []):
        rs = int((s or {}).get('race_status', 0) or 0)
        t = int((s or {}).get('time_ms', 0) or 0)
        if rs != 0 or t > 0:
            print(f'result {uname} flat={i} rs={rs} time={t} '
                  f'submitted_at={s.get("submitted_at")}')
print('== done ==')
EOF
