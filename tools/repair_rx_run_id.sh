#!/bin/bash
# Backfill best_times.run_id for rallycross rows that were never attributed to
# their producing stage_runs row. RX best rows are single-lap PostTime records:
# the stage-detail follow-up carries the STAGE TOTAL (slower than the best lap),
# so _upsert_best can never link them — only _stamp_rx_run_id can, and its old
# 250 ms tolerance missed runs where the game-clock PostTime drifted >250 ms
# from the UDP-derived splits (production 2026-11-28: 425 ms).
#
# Matching (mirrors the live _stamp_rx_run_id, widened for history):
#   - same username + vehicle_id + track_model_id; conditions compared in SQL
#   - row's compound must equal the run's (an unresolved comp=0 row matches any)
#   - lap match: stored ms within 1000 ms of one of the run's per-lap split
#     times, and the row was filed within [run.received_at - 900 s, +5 s]
#     (the best-lap PostTime lands DURING the run; max 12 laps ~ 12 min)
#   - no-splits fallback (legacy clients): pure proximity, |row.submitted_at -
#     run.received_at| <= 300 s
# Only run_id is set — submitted_at and every payload column are untouched.
# Usage: repair_rx_run_id.sh [apply]   (default: dry run)
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

apply = len(sys.argv) > 1 and sys.argv[1] == 'apply'

sys.path.insert(0, '/opt/dr3')
from dr2server.game_data import Track
RX_TRACKS = {int(t) for t in Track if t.discipline == "rallycross"}

TOL_MS, WIN_S, PROX_S = 1000, 900, 300

cur.execute("""SELECT id, username, vehicle_id, track, conditions, tyre_compound,
    stage_time_ms, submitted_at
    FROM best_times WHERE run_id IS NULL""")
rows = [r for r in cur.fetchall() if int(r[3]) in RX_TRACKS]
if not rows:
    print('== no unattributed RX best rows ==')
    sys.exit(0)

fixed = skipped = 0
for rid, uname, veh, track, cond, comp, ms, submitted in rows:
    if submitted is None:
        print(f'SKIP id={rid} {uname} car={veh} track={track}: no submitted_at')
        skipped += 1
        continue
    cond_sql = "AND conditions_id=%s" if str(cond).isdigit() else ""
    cond_arg = [int(cond)] if str(cond).isdigit() else []
    cur.execute(f"""SELECT id, event_id, flat_stage_index, received_at, tyre_compound,
        splits_json FROM stage_runs
        WHERE username=%s AND vehicle_id=%s AND track_model_id=%s {cond_sql}
        ORDER BY received_at DESC LIMIT 200""", (uname, veh, int(track), *cond_arg))
    runs = cur.fetchall()
    lap_cands, prox_cands = [], []
    for run_id, event_id, flat, received, rcomp, splits_raw in runs:
        if received is None or comp not in (0, int(rcomp or 0)):
            continue
        age = (received - submitted).total_seconds()  # >0: run ended after row filed
        splits = json.loads(splits_raw) if splits_raw else None
        if splits:
            diff = min(abs(ms - lap * 1000) for lap in splits)
            if diff <= TOL_MS and -5 <= age <= WIN_S:
                lap_cands.append((diff, abs(age), run_id, event_id, flat, received))
        elif abs(age) <= PROX_S:
            prox_cands.append((abs(age), run_id, event_id, flat, received))
    if lap_cands:
        diff, aage, run_id, event_id, flat, received = min(lap_cands)
        print(f"{'APPLY' if apply else 'DRY  '} id={rid} {uname} car={veh} track={track} "
              f"comp={comp} stored={ms} ms -> run={run_id} (event={event_id} flat={flat} "
              f"received={received}) lap diff={diff} ms, row filed {-aage:.0f}s before run end")
        if apply:
            cur.execute("UPDATE best_times SET run_id=%s WHERE id=%s", (run_id, rid))
            fixed += 1
    elif prox_cands:
        aage, run_id, event_id, flat, received = min(prox_cands)
        print(f"{'APPLY' if apply else 'DRY  '} id={rid} {uname} car={veh} track={track} "
              f"comp={comp} stored={ms} ms -> run={run_id} (event={event_id} flat={flat} "
              f"received={received}) no splits, proximity {aage:.0f}s")
        if apply:
            cur.execute("UPDATE best_times SET run_id=%s WHERE id=%s", (run_id, rid))
            fixed += 1
    else:
        print(f'SKIP id={rid} {uname} car={veh} track={track} comp={comp} stored={ms} ms '
              f'submitted={submitted}: no candidate among {len(runs)} runs')
        skipped += 1

if apply:
    conn.commit()
print(f'== {fixed} rows updated, {skipped} skipped ==')
EOF
