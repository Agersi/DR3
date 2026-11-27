#!/bin/bash
# Dump stage_runs rows for club events. Usage:
#   inspect_stage_runs.sh [EVENT_ID] [FILTER]
# FILTER: "dnf" (player_finished=0 or race_status!=0), "flatnull" (flat_stage_index IS NULL), or empty (all).
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$@" <<'EOF'
import sys, re
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

if flt == 'results-dnf':
    # DNF entries inside results.stages_json (what the board actually renders).
    import json as _json
    cur.execute("""SELECT r.event_id, r.username, r.stages_json
        FROM club_results r JOIN events e ON e.id = r.event_id
        WHERE e.club_id IS NOT NULL""")
    for ev_id, uname, sj in cur.fetchall():
        try:
            stages = _json.loads(sj) if sj else []
        except ValueError:
            continue
        for i, s in enumerate(stages or []):
            if not s:
                continue
            rs = int(s.get('race_status', 0) or 0)
            t = int(s.get('time_ms', 0) or 0)
            if rs == 0 and t <= 0:
                continue
            if rs != 0:
                print(f'event={ev_id} user={uname} flat={i} rs={rs} '
                      f'time={t} vehicle={s.get("vehicle_id")} '
                      f'tyre={s.get("tyre_compound")} rem={s.get("tyres_remaining")} '
                      f'spares={s.get("spare_tyres")} '
                      f'blob={"yes" if s.get("tuning_setup_b64") else "no"} '
                      f'dmg={"yes" if s.get("comp_damage") else "no"}')
    print('== results DNF sweep done ==')
    sys.exit(0)

where = "source_type='club'"
if event_id:
    where += f" AND event_id='{event_id}'"
if flt == 'dnf':
    where += " AND (player_finished=0 OR race_status<>0)"
elif flt == 'flatnull':
    where += " AND flat_stage_index IS NULL"

cur.execute(f"""SELECT id, username, event_id, challenge_id, event_index, stage_index,
    track_model_id, vehicle_id, time_ms, player_finished, player_end_status,
    race_position, flat_stage_index, input_method, camera_position, tyre_compound,
    vr_enabled, using_wheel, using_assists, assists_enabled,
    LENGTH(splits_json) AS splits_len, LENGTH(comp_damage_json) AS dmg_len, received_at
    FROM stage_runs WHERE {where} ORDER BY received_at DESC LIMIT 40""")
cols = [d[0] for d in cur.description]
n = 0
for r in cur.fetchall():
    print(dict(zip(cols, r)))
    n += 1
print(f'== {n} rows ==')

print('== summary by source_type ==')
cur.execute("""SELECT source_type, COUNT(*), MIN(received_at), MAX(received_at)
    FROM stage_runs GROUP BY source_type""")
for r in cur.fetchall():
    print(r)
EOF
