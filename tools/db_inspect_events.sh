#!/bin/bash
# Inspect v2 championship events on the production DB: settings keys + per-leg
# location/class/stages/rx_laps, plus a results summary. Run via:
#   python tools\vps.py --run-script tools\db_inspect_events.sh
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - <<'EOF'
import json, re
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

print('== events (v2 legs detail) ==')
cur.execute("SELECT id, name, club_id, type, schema_version, settings_json, legs_json FROM events WHERE schema_version=2 ORDER BY start_time")
for eid, name, club_id, etype, sv, settings, legs in cur.fetchall():
    print(f'--- {eid} | {name} | club={club_id} | type={etype}')
    try:
        s = json.loads(settings) if isinstance(settings, str) else (settings or {})
    except Exception:
        s = {}
    print('  settings keys:', sorted(s.keys()) if isinstance(s, dict) else type(s))
    for k in ('scoring_system','power_stage','use_inv_vehicle','vehicle_ids','challenge_type','scoring_type','challenge_mode'):
        if isinstance(s, dict) and k in s:
            print(f'  settings.{k} = {s[k]!r}')
    try:
        L = json.loads(legs) if isinstance(legs, str) else (legs or [])
    except Exception:
        L = []
    for i, leg in enumerate(L):
        loc = leg.get('location_id') or leg.get('location')
        print(f'  leg[{i}]: location_id={loc!r} class={leg.get("car_class")!r} stages={leg.get("stage_count", leg.get("stages"))!r} rx_laps={leg.get("rx_laps")!r}')

print()
print('== results per event ==')
cur.execute("SELECT id, username, vehicle_id, stages_json, in_progress_json FROM results ORDER BY id DESC LIMIT 60")
for rid, user, veh, stages, inprog in cur.fetchall():
    try:
        st = json.loads(stages) if isinstance(stages, str) else (stages or [])
    except Exception:
        st = []
    print(f'result {rid} user={user} veh={veh} stages_done={len(st)}')
EOF
