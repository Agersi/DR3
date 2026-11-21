#!/bin/bash
# Merges orphaned best_times placeholders into their existing per-compound
# sibling and deletes the placeholder — the state that makes the boot-time
# tyre-compound backfill crash with a unique-key violation (a compound=0 row
# whose resolved compound already exists in the same 4-tuple group).
#
# A placeholder is orphaned when the stage-detail channel's time differs from
# the PostTime's by even 1 ms: _upsert_best then creates a fresh per-compound
# row instead of adopting the placeholder. The merge keeps the strictly faster
# time and fills payload fields the sibling lacks, mirroring _upsert_best's
# replace/fill rules. Idempotent — a second run finds nothing to do.
#
# Usage: fix_best_times_orphaned_placeholders.sh [--apply] [username]
#   default is DRY RUN (prints intended actions, changes nothing)
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$@" <<'EOF'
import re, sys
from datetime import datetime

args = [a for a in sys.argv[1:]]
apply = '--apply' in args
args = [a for a in args if a != '--apply']
uname = args[0] if args else None

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

where = ' AND username=%s' if uname else ''
argsq = [uname] if uname else []

# --- replicate the boot backfill's resolution -----------------------------
pool = {}
cur.execute('''SELECT username, vehicle_id, track_model_id, conditions_id,
                      time_ms, tyre_compound, received_at FROM stage_runs''')
for sr_u, sr_v, sr_t, sr_c, sr_ms, sr_cp, sr_at in cur.fetchall():
    if not sr_cp or not sr_v or sr_t is None:
        continue
    key = (sr_u, sr_v, str(sr_t)[:8], str(sr_c)[:8] if sr_c is not None else '0')
    pool.setdefault(key, []).append((sr_ms or 0, sr_cp, sr_at))

cur.execute('''SELECT id, username, vehicle_id, track, conditions, stage_time_ms
               FROM best_times WHERE tyre_compound=0''' + where, argsq)
zeros = cur.fetchall()

cur.execute('''SELECT id, username, vehicle_id, track, conditions, tyre_compound,
                      stage_time_ms, vclass, category, livery_id, using_wheel,
                      using_assists, ghost_data_b64, splits_json
               FROM best_times WHERE tyre_compound>0''' + where, argsq)
siblings = {}
for r in cur.fetchall():
    siblings.setdefault((r[2], r[3], r[4], r[1]), []).append(r)

actions = []
for zid, un, vid, trk, cond, tms in zeros:
    cands = pool.get((un, vid, str(trk)[:8], str(cond)[:8])) or []
    pick = next(((ms, c, at) for ms, c, at in cands if ms == (tms or 0)), None)
    if pick is None and cands:
        pick = max(cands, key=lambda t: t[2] or datetime.min)
    if pick is None:
        continue
    target = int(pick[1])
    sib = next((s for s in siblings.get((vid, trk, cond, un), [])
                if s[5] == target), None)
    if sib is None:
        continue  # plain backfill handles it; no collision
    actions.append((zid, un, vid, trk, cond, tms, target, sib))

print(f'mode={"APPLY" if apply else "DRY-RUN"} username={uname or "ALL"} '
      f'orphans_to_merge={len(actions)}')
for zid, un, vid, trk, cond, tms, target, sib in actions:
    sid = sib[0]
    stime = sib[6]
    print(f'-- merge placeholder id={zid} (t={tms}) into sibling id={sid} '
          f'(compound={target}, t={stime}) user={un!r} v={vid} t={trk!r} c={cond!r}')
    if tms < stime:
        print(f'   faster: sibling time {stime} -> {tms}; payload copied from placeholder')
    else:
        print('   slower/equal: only NULL payload fields on sibling filled')

if not actions:
    sys.exit(0)
if not apply:
    print('DRY RUN — re-run with --apply to execute')
    sys.exit(0)

for zid, un, vid, trk, cond, tms, target, sib in actions:
    sid = sib[0]
    stime, svclass, scat, sliv, swheel, sassists, sghost, ssplits = sib[6:]
    cur.execute('''SELECT vclass, category, livery_id, using_wheel, using_assists,
                          ghost_data_b64, splits_json FROM best_times WHERE id=%s''', (zid,))
    pvclass, pcat, pliv, pwheel, passists, pghost, psplits = cur.fetchone()
    if tms < stime:
        cur.execute('''UPDATE best_times SET stage_time_ms=%s, vclass=%s, category=%s,
                           livery_id=COALESCE(%s, livery_id),
                           using_wheel=COALESCE(%s, using_wheel),
                           using_assists=COALESCE(%s, using_assists),
                           ghost_data_b64=COALESCE(%s, ghost_data_b64),
                           splits_json=COALESCE(%s, splits_json) WHERE id=%s''',
                    (tms, pvclass, pcat or scat, pliv, pwheel, passists, pghost, psplits, sid))
    else:
        cur.execute('''UPDATE best_times SET livery_id=COALESCE(livery_id, %s),
                           using_wheel=COALESCE(using_wheel, %s),
                           using_assists=COALESCE(using_assists, %s),
                           ghost_data_b64=COALESCE(ghost_data_b64, %s),
                           splits_json=COALESCE(splits_json, %s) WHERE id=%s''',
                    (pliv, pwheel, passists, pghost, psplits, sid))
    cur.execute('DELETE FROM best_times WHERE id=%s', (zid,))
conn.commit()
print(f'applied: {len(actions)} placeholder(s) merged + deleted')

# --- verify no collision remains -------------------------------------------
cur.execute('''SELECT COUNT(*) FROM best_times b WHERE tyre_compound=0 AND EXISTS
    (SELECT 1 FROM best_times s WHERE s.vehicle_id=b.vehicle_id AND s.track=b.track
      AND s.conditions=b.conditions AND s.username=b.username
      AND s.tyre_compound>0)''')
print('zero rows sharing a group with any sibling:', cur.fetchone()[0])
EOF
