#!/bin/bash
# Dump every result row that carries a DNF stage entry (race_status != 0),
# with event context: club, event name, per-leg stage counts, the global
# position of each DNF entry mapped to leg/stage, and whether the wrecked
# stage was mid-leg or the leg's last stage. Stage entries are stored as a
# flat list indexed by GLOBAL stage position (no explicit index field).
# Used for terminal-damage crash investigation (backlog #32).
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

def leg_bounds(leg_counts):
    # global position -> (leg_index, stage_in_leg, is_last_stage_of_leg)
    out, g = [], 0
    for li, c in enumerate(leg_counts):
        for si in range(c):
            out.append((li, si, si == c - 1))
        g += c
    return out

cur.execute("SELECT id, event_id, username, car, vehicle_id, attempts_used, total_time_ms, stages_json FROM results")
rows = cur.fetchall()
found = 0
for rid, event_id, username, car, vehicle_id, attempts, total_ms, stages_json in rows:
    try:
        stages = json.loads(stages_json or '[]')
    except Exception:
        continue
    dnf_pos = [p for p, s in enumerate(stages) if int(s.get('race_status', 0) or 0) != 0]
    if not dnf_pos:
        continue
    found += 1
    cur.execute("SELECT name, club_id, schema_version, legs_json, stages_json FROM events WHERE id=%s", (event_id,))
    ev = cur.fetchone()
    if not ev:
        print(f'--- result {rid} user={username} event={event_id} (event row missing)')
        for p in dnf_pos:
            print(f'    DNF at global pos {p}: {stages[p]}')
        continue
    name, club_id, sv, legs_json, ev_stages_json = ev
    try:
        legs = json.loads(legs_json or '[]')
    except Exception:
        legs = []
    if sv == 2 and legs:
        leg_counts = [len(l.get('stages', []) or []) for l in legs]
    else:
        leg_counts = [len(json.loads(ev_stages_json or '[]'))]
    bounds = leg_bounds(leg_counts)
    print(f'--- result {rid} user={username} car={car} vehicle_id={vehicle_id} '
          f'attempts_used={attempts} total_time_ms={total_ms}')
    print(f'    event={event_id} name={name!r} club={club_id} schema_v{sv} leg_stage_counts={leg_counts}')
    for p, s in enumerate(stages):
        li, si, last = bounds[p] if p < len(bounds) else ('?', '?', False)
        tag = 'DNF' if p in dnf_pos else ('done' if s.get('time_ms', 0) > 0 else 'empty')
        print(f"    [{tag}] pos={p} leg={li} stage={si}/last={last} "
              f"time_ms={s.get('time_ms')} race_status={s.get('race_status')} "
              f"submitted_at={s.get('submitted_at')} vehicle_id={s.get('vehicle_id')}")
if not found:
    print('no DNF stage entries found')
EOF
