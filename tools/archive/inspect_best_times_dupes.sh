#!/bin/bash
# Reports best_times rows that would collide when the boot-time tyre-compound
# backfill runs: groups sharing (vehicle_id, track, conditions, username) with
# more than one row, plus a simulation of which zero-compound rows resolve to
# a compound already present in their group. Usage: inspect_best_times_dupes.sh [username]
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$@" <<'EOF'
import re, sys
env = {}
for line in open('.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k] = v
m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)', env['DATABASE_URL'])
import pymysql
conn = pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306),
                       user=m.group(1), password=m.group(2), database=m.group(5).split('?')[0])
cur = conn.cursor()

print('== uq_best_times_key ==')
cur.execute("SHOW INDEX FROM best_times WHERE Key_name='uq_best_times_key'")
for r in cur.fetchall():
    print('col', r[3], 'seq', r[5])

uname = sys.argv[1] if len(sys.argv) > 1 else None
where = 'WHERE username=%s' if uname else ''
args = [uname] if uname else []

print(f'== multi-row groups (username={uname or "ALL"}) ==')
cur.execute(f'''SELECT vehicle_id, track, conditions, username, COUNT(*) c
    FROM best_times {where} GROUP BY vehicle_id, track, conditions, username
    HAVING c > 1''', args)
groups = cur.fetchall()
print('groups:', len(groups))
for g in groups:
    vid, trk, cond, un, c = g
    print(f'-- group vehicle={vid} track={trk!r} cond={cond!r} user={un!r} rows={c}')
    cur.execute('''SELECT id, HEX(username), HEX(track), HEX(conditions),
                          tyre_compound, stage_time_ms, livery_id, vclass
                   FROM best_times WHERE vehicle_id=%s AND track=%s
                     AND conditions=IF(%s IS NULL, conditions, %s)
                     AND username=%s ORDER BY id''', (vid, trk, cond, cond, un))
    for r in cur.fetchall():
        print(f'   id={r[0]} user_hex={r[1]} track_hex={r[2]} cond_hex={r[3]} '
              f'compound={r[4]} time_ms={r[5]} livery={r[6]} vclass={r[7]!r}')

print('== backfill collision simulation (username filter applied) ==')
cur.execute('''SELECT id, username, vehicle_id, track, conditions, stage_time_ms
               FROM best_times WHERE tyre_compound=0'''
            + (' AND username=%s' if uname else ''), args)
zeros = cur.fetchall()
print('zero rows in scope:', len(zeros))
pool = {}
cur.execute('''SELECT username, vehicle_id, track_model_id, conditions_id,
                      time_ms, tyre_compound, received_at FROM stage_runs''')
from datetime import datetime
for sr_u, sr_v, sr_t, sr_c, sr_ms, sr_cp, sr_at in cur.fetchall():
    if not sr_cp or not sr_v or sr_t is None:
        continue
    key = (sr_u, sr_v, str(sr_t)[:8], str(sr_c)[:8] if sr_c is not None else '0')
    pool.setdefault(key, []).append((sr_ms or 0, sr_cp, sr_at))

existing = {}
cur.execute('''SELECT vehicle_id, track, conditions, username, tyre_compound, id
               FROM best_times WHERE tyre_compound>0'''
            + (' AND username=%s' if uname else ''), args)
for vid, trk, cond, un, cp, rid in cur.fetchall():
    existing.setdefault((vid, trk, cond, un), []).append((cp, rid))

collisions = 0
for zid, un, vid, trk, cond, tms in zeros:
    cands = pool.get((un, vid, str(trk)[:8], str(cond)[:8])) or []
    pick = next(((ms, c, at) for ms, c, at in cands if ms == (tms or 0)), None)
    if pick is None and cands:
        pick = max(cands, key=lambda t: t[2] or datetime.min)
    if pick is None:
        continue
    target = int(pick[1])
    sibs = existing.get((vid, trk, cond, un), [])
    other_zeros = [z for z in zeros if z != (zid, un, vid, trk, cond, tms)
                   and z[2] == vid and z[3] == trk and z[4] == cond and z[1] == un]
    if any(cp == target for cp, _ in sibs):
        collisions += 1
        print(f'COLLIDE id={zid} user={un!r} v={vid} t={trk!r} c={cond!r} '
              f'time={tms} -> compound {target} (row already has it: {[i for cp, i in sibs if cp == target]})')
    elif other_zeros:
        collisions += 1
        print(f'COLLIDE id={zid} user={un!r} v={vid} t={trk!r} c={cond!r} '
              f'time={tms} -> compound {target} (sibling zero rows: {[z[0] for z in other_zeros]})')
print('collisions:', collisions)
EOF
