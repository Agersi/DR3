#!/bin/bash
# Read-only: dump a club's championship layout (mode, windows, per-leg class /
# duration / stage counts) plus one driver's completed stages mapped onto the
# legs — used to investigate mid-season joiners / missed-round bugs.
# Run via:
#   python tools/vps.py --run-script tools/inspect_club_event_progress.sh CLUB_ID [USERNAME]
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
import pymysql
conn = pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306), user=m.group(1), password=m.group(2), database=m.group(5).split('?')[0])
cur = conn.cursor()

club_id = sys.argv[1] if len(sys.argv) > 1 else None
username = sys.argv[2] if len(sys.argv) > 2 else None

cur.execute("SELECT id, name FROM clubs WHERE id=%s", (club_id,))
crow = cur.fetchone()
print(f"===== club {club_id}: {crow[1] if crow else 'NOT FOUND'} =====")

cur.execute("SELECT id, name, legs_json, settings_json, start_time, end_time, active "
            "FROM events WHERE club_id=%s ORDER BY start_time", (club_id,))
events = cur.fetchall()
eids = []
for eid, name, legs_json, settings_json, st, et, active in events:
    eids.append(eid)
    settings = json.loads(settings_json or '{}')
    legs = json.loads(legs_json or '[]')
    print(f"===== {eid} | {name} | mode={settings.get('mode', '?')} | "
          f"active={active} | start={st} end={et} | legs={len(legs)} =====")
    for i, leg in enumerate(legs):
        stages = leg.get('stages') or []
        print(f"  leg[{i}]: class={leg.get('car_class')!r} loc={leg.get('location')!r} "
              f"dur={leg.get('duration')} stages={len(stages)}")

if username and eids:
    marks = ','.join(['%s'] * len(eids))
    cur.execute(f"SELECT event_id, challenge_id, event_index, stage_index, flat_stage_index, "
                f"vehicle_id, time_ms, player_finished, input_method, camera_position, "
                f"(splits_json IS NOT NULL), received_at FROM stage_runs "
                f"WHERE username=%s AND event_id IN ({marks}) "
                f"ORDER BY received_at", [username] + eids)
    print(f"\n===== stage_runs for {username} =====")
    for r in cur.fetchall():
        print(f"  {r[0]} ch={r[1]} ev={r[2]} st={r[3]} flat={r[4]} veh={r[5]} "
              f"time={r[6]}ms fin={r[7]} in={r[8]} cam={r[9]} splits={r[10]} at={r[11]}")

    cur.execute(f"SELECT event_id, car, total_time_ms, stages_json, in_progress_json "
                f"FROM club_results WHERE username=%s AND event_id IN ({marks})",
                [username] + eids)
    for eid, car, total, sj, ipj in cur.fetchall():
        print(f"\n===== club_results {eid} | car={car} | total={total}ms =====")
        for i, s in enumerate(json.loads(sj or '[]')):
            slim = {k: v for k, v in (s or {}).items()
                    if k not in ('tuning_setup_b64', 'comp_damage', 'vehicle_mud')}
            print(f"  flat[{i}]: {json.dumps(slim)}")
        print(f"  in_progress: {ipj}")

    cur.execute(f"SELECT flat_stage_index FROM club_stage_states "
                f"WHERE username=%s AND event_id IN ({marks}) ORDER BY flat_stage_index",
                [username] + eids)
    rows = [r[0] for r in cur.fetchall()]
    print(f"\n===== club_stage_states rows: {rows} =====")
EOF
