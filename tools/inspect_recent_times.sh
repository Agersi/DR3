#!/bin/bash
# Dump what the front page's recent-club-times table shows (the same filter as
# pages/views._recent_stage_times: time_ms > 0 AND submitted_at), flagging DNF
# close-out entries (race_status != 0). Usage:
#   inspect_recent_times.sh [dnf|all]   (default: dnf)
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
user, pw, host, port, dbname = m.group(1), m.group(2), m.group(3), int(m.group(4) or 3306), m.group(5).split('?')[0]
import pymysql
conn = pymysql.connect(host=host, port=port, user=user, password=pw, database=dbname)
cur = conn.cursor()

flt = sys.argv[1] if len(sys.argv) > 1 else 'dnf'

if flt == 'user':
    uname = sys.argv[2]
    cur.execute("""SELECT e.id, e.name, r.stages_json FROM club_results r
        JOIN events e ON e.id = r.event_id
        WHERE r.username = %s AND e.club_id IS NOT NULL""", (uname,))
    evs = []
    for ev_id, ev_name, sj in cur.fetchall():
        try:
            stages = json.loads(sj) if sj else []
        except ValueError:
            continue
        for i, s in enumerate(stages or []):
            s = s or {}
            t = int(s.get('time_ms', 0) or 0)
            sa = s.get('submitted_at')
            rs = int(s.get('race_status', 0) or 0)
            if t <= 0 and not sa:
                continue
            evs.append((sa, ev_id, ev_name, i, t, rs))
    evs.sort(reverse=True)
    for sa, ev_id, ev_name, i, t, rs in evs[:30]:
        print(f'entry sa={sa} event={ev_id} ({ev_name}) flat={i} time={t} rs={rs}')
    ids = sorted({ev_id for _, ev_id, _, _, _, _ in evs})
    if ids:
        marks = ','.join(['%s'] * len(ids))
        cur.execute(f"""SELECT event_id, flat_stage_index, time_ms, player_finished,
            player_end_status, distance_m, received_at FROM stage_runs
            WHERE username = %s AND event_id IN ({marks})
            ORDER BY received_at""", [uname] + ids)
        print('--- stage_runs for those events ---')
        for row in cur.fetchall():
            print(row)
    sys.exit(0)

if flt == 'counts':
    cur.execute("SELECT type, COUNT(*) FROM events GROUP BY type")
    print('events by type:', cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM club_results")
    print('club_results rows:', cur.fetchone()[0])
    cur.execute("""SELECT e.id, e.name, e.type, r.username, LEFT(r.stages_json, 400)
        FROM club_results r JOIN events e ON e.id = r.event_id
        ORDER BY r.id DESC LIMIT 5""")
    for row in cur.fetchall():
        print(row)
    sys.exit(0)

cur.execute("""SELECT e.id, e.name, e.active, e.end_time, r.username, r.stages_json
    FROM club_results r JOIN events e ON e.id = r.event_id
    WHERE e.club_id IS NOT NULL""")
shown_total = 0
rows = []
for ev_id, ev_name, active, end_time, uname, sj in cur.fetchall():
    try:
        stages = json.loads(sj) if sj else []
    except ValueError:
        continue
    for i, s in enumerate(stages or []):
        s = s or {}
        t = int(s.get('time_ms', 0) or 0)
        sa = s.get('submitted_at')
        if t <= 0 or not sa:
            continue
        shown_total += 1
        rs = int(s.get('race_status', 0) or 0)
        if flt == 'dnf' and rs == 0:
            continue
        rows.append((sa, ev_id, ev_name, active, end_time, uname, i, t, rs,
                     s.get('vehicle_id')))
rows.sort(reverse=True)
for sa, ev_id, ev_name, active, end_time, uname, i, t, rs, vid in rows:
    print(f'sa={sa} event={ev_id} ({ev_name}) active={active} '
          f'end={end_time} user={uname} flat={i} time={t} rs={rs} vid={vid}')
print(f'== {len(rows)} rows (filter={flt}); total shown in recent times: {shown_total} ==')
EOF
