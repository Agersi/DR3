#!/bin/bash
# Repair stage_runs rows for one club event that are missing flat_stage_index
# (wreck close-outs recorded before v0.1.93, or dropped by the pre-fix web
# merge). The flat ordinal is recomputed from the event's legs layout: the row's
# (track_model_id, stage_index) must match exactly one leg stage.
# Usage: repair_flat_index.sh EVENT_ID [apply]   (default: dry run)
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
apply = len(sys.argv) > 2 and sys.argv[2] == 'apply'
if not event_id:
    print('usage: repair_flat_index.sh EVENT_ID [apply]')
    sys.exit(1)

cur.execute("SELECT legs_json FROM events WHERE id=%s", (event_id,))
row = cur.fetchone()
if not row or not row[0]:
    print(f'event {event_id} not found or has no legs_json')
    sys.exit(1)
legs = json.loads(row[0])
layout = []  # (leg_index, offset, [(stage_idx, track_id), ...])
offset = 0
for i, leg in enumerate(legs or []):
    stages = leg.get('stages') or []
    layout.append((i, offset, [(j, s.get('track_id')) for j, s in enumerate(stages)]))
    offset += len(stages)

cur.execute("""SELECT id, username, challenge_id, stage_index, track_model_id,
    time_ms, player_finished
    FROM stage_runs WHERE event_id=%s AND flat_stage_index IS NULL""", (event_id,))
rows = cur.fetchall()
if not rows:
    print(f'== no NULL-flat rows for {event_id} ==')
    sys.exit(0)

fixed = 0
for rid, uname, chid, sidx, track, tms, fin in rows:
    matches = []
    for li, off, stages in layout:
        for sj, tid in stages:
            if sj == sidx and tid == track:
                matches.append((li, off + sj))
    if len(matches) != 1:
        print(f'SKIP id={rid} {uname}: {len(matches)} layout matches '
              f'(track={track} stage={sidx})')
        continue
    li, flat = matches[0]
    print(f"{'APPLY' if apply else 'DRY  '} id={rid} {uname} challenge={chid} "
          f'stage={sidx} track={track} -> leg {li} flat={flat} (time={tms})')
    if apply:
        cur.execute("UPDATE stage_runs SET flat_stage_index=%s WHERE id=%s",
                    (flat, rid))
        fixed += 1

if apply:
    conn.commit()
print(f'== {fixed} rows updated ==')
EOF
