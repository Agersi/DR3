#!/bin/bash
# Diagnostic: dump everything the site leaderboard needs for one track ???
# best_times rows, their producing stage_runs (source/event/flat/splits/damage),
# the referenced club events' leg layout, and the club_results stage entries.
# Use when a board row is missing its source link / spares / damage payload.
# Usage: diag_track_board.sh <track_model_id>
set -e
TRACK="${1:?usage: diag_track_board.sh <track_model_id>}"
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$TRACK" <<'EOF'
import json, re, sys
track = int(sys.argv[1])
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

print(f'== best_times for track {track} ==')
cur.execute("""SELECT id, username, vehicle_id, stage_time_ms, tyre_compound, conditions, run_id,
    splits_json IS NOT NULL AS has_splits, ghost_data_b64 IS NOT NULL AS has_ghost, submitted_at
    FROM best_times WHERE track = %s ORDER BY stage_time_ms""", (str(track),))
bts = cur.fetchall()
for r in bts:
    print(f'bt#{r[0]} {r[1]:<12} veh={r[2]:<5} {r[3]:>9} ms  comp={r[4]} cond={r[5]} run_id={r[6]} '
          f'splits={bool(r[7])} ghost={bool(r[8])} updated={r[9]}')
if not bts:
    print('(none)')

run_ids = sorted({r[6] for r in bts if r[6]})
print(f'== all stage_runs on track {track} (newest first, max 40) ==')
cur.execute("""SELECT id, username, vehicle_id, source_type, event_id, challenge_id, stage_index,
    flat_stage_index, time_ms, player_finished, received_at,
    LENGTH(splits_json), LENGTH(comp_damage_json)
    FROM stage_runs WHERE track_model_id = %s ORDER BY received_at DESC LIMIT 40""", (track,))
seen_run_ids = set()
all_event_ids = set()
for r in cur.fetchall():
    seen_run_ids.add(r[0])
    if r[4]:
        all_event_ids.add(r[4])
    print(f'run#{r[0]} {r[1]:<12} veh={r[2]:<5} src={r[3]:<8} event={r[4]} chal={r[5]} stg={r[6]} flat={r[7]} '
          f'time={r[8]} fin={bool(r[9])} rx={r[10]} splits_len={r[11]} cddmg_len={r[12]}')

missing_runs = [rid for rid in run_ids if rid not in seen_run_ids]
if missing_runs:
    print(f'== dangling best_times.run_id (no stage_runs row) == {missing_runs}')

event_ids = set()
if run_ids:
    marks = ','.join(['%s'] * len(run_ids))
    cur.execute(f"""SELECT DISTINCT event_id FROM stage_runs
        WHERE id IN ({marks}) AND event_id IS NOT NULL""", run_ids)
    event_ids = {r[0] for r in cur.fetchall()}
event_ids = sorted(event_ids | all_event_ids)
if event_ids:
    print('== referenced club events ==')
    marks = ','.join(['%s'] * len(event_ids))
    cur.execute(f"""SELECT id, club_id, name, settings_json, legs_json FROM events WHERE id IN ({marks})""", event_ids)
    for eid, club, name, settings, legs in cur.fetchall():
        s = json.loads(settings or '{}')
        lgs = json.loads(legs or '[]')
        layout = ' | '.join(f"leg{i}: " + ",".join(str(st.get('track_id')) for st in lg.get('stages', [])) for i, lg in enumerate(lgs))
        print(f'{eid} club={club} mode={s.get("mode")} name={name!r}')
        print(f'   {layout}')
    print('== club_results stage entries (these events) ==')
    bt_names = {r[1] for r in bts}
    cur.execute(f"""SELECT event_id, username, stages_json FROM club_results WHERE event_id IN ({marks})""", event_ids)
    for eid, uname, sj in cur.fetchall():
        stages = json.loads(sj or '[]')
        if uname not in bt_names:
            print(f'{eid} {uname}: {len(stages)} stage entries (not on this board - omitted)')
            continue
        print(f'{eid} {uname}: {len(stages)} stage entries')
        for i, se in enumerate(stages):
            keep = {k: v for k, v in (se or {}).items() if k not in ('tuning_setup_b64',)}
            print(f'   [{i}] {json.dumps(keep, sort_keys=True)[:400]}')
print('== done ==')
EOF

