#!/usr/bin/env python3
"""Decode + diff comp_damage across consecutive stages of a club event.

Reads club_stage_states (heavy side table) + club_results stage entries,
unpacks the six 4xuint8 packed fields byte-wise, and prints per-stage values
plus deltas vs the previous stage for each driver. Decreases are flagged
with 'v' (car reset / tyre swap / repair signals).

Run on the VPS:
    /opt/dr3/.venv/bin/python analyze_comp_damage_event.py EVENT_ID [USERNAME]
"""
import json
import os
import re
import sys

FLOATS = ('clutch', 'engine', 'exhaust', 'lights', 'radiator')
PACKED = ('brakes', 'dampers', 'springs', 'wheels_wear', 'wheels_impact', 'bodywork')
SCALARS = ('gearbox', 'turbo', 'diff_wear', 'diff_impact', 'quick_repairs')

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import pymysql  # noqa: E402

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             os.environ["DATABASE_URL"])
conn = pymysql.connect(host=p.group(3), port=int(p.group(4) or 3306),
                       user=p.group(1), password=p.group(2),
                       database=p.group(5).split("?")[0])
cur = conn.cursor()

event_id = sys.argv[1] if len(sys.argv) > 1 else None
user_filter = sys.argv[2] if len(sys.argv) > 2 else None
if not event_id:
    sys.exit("usage: analyze_comp_damage_event.py EVENT_ID [USERNAME]")

cur.execute("SELECT name, settings_json FROM events WHERE id=%s", (event_id,))
row = cur.fetchone()
if not row:
    sys.exit(f"unknown event: {event_id}")
settings = json.loads(row[1] or "{}")
print(f"event: {event_id} ({row[0]}) hardcore={settings.get('hardcore_damage')}")

q = ("SELECT username, flat_stage_index, comp_damage_json "
     "FROM club_stage_states WHERE event_id=%s")
args = [event_id]
if user_filter:
    q += " AND username=%s"
    args.append(user_filter)
q += " ORDER BY username, flat_stage_index"
cur.execute(q, args)

by_user = {}
for username, idx, dam in cur.fetchall():
    if not dam:
        continue
    by_user.setdefault(username, {})[idx] = json.loads(dam)

if user_filter and user_filter not in by_user:
    sys.exit(f"user {user_filter} has no comp_damage rows in {event_id}")

cur.execute("SELECT username, car, stages_json FROM club_results WHERE event_id=%s",
            (event_id,))
stage_meta = {}
for username, car, sj in cur.fetchall():
    try:
        entries = json.loads(sj or "[]")
    except ValueError:
        entries = []
    stage_meta[username] = (car, {e.get("stage_index", i): e
                                  for i, e in enumerate(entries)})

def unpack(v):
    return [(v >> (8 * i)) & 0xFF for i in range(4)]

def fmt_packed(vals):
    return " ".join(f"{b:3d}" for b in vals)

for username in sorted(by_user):
    car, meta = stage_meta.get(username, (None, {}))
    stages = by_user[username]
    print(f"\n=== {username} ({car or '?'}) — {len(stages)} damaged stages ===")
    prev = None
    for idx in sorted(stages):
        d = stages[idx]
        m = meta.get(idx, {})
        status = m.get("race_status", 0)
        tms = m.get("time_ms", 0)
        tag = "" if status == 0 else f"  [status={status}]"
        print(f"--- stage {idx}  time={tms / 1000:.2f}s{tag}")
        fl = " ".join(f"{k}={d.get(k, 0):.3f}" for k in FLOATS)
        print(f"    floats : {fl}")
        for k in PACKED:
            val = fmt_packed(unpack(d.get(k, 0)))
            print(f"    {k:14s}: [{val}]")
        sc = " ".join(f"{k}={d.get(k, 0):,}" for k in SCALARS)
        print(f"    scalars: {sc}")
        if prev is not None:
            pd = prev[1]
            dfl = " ".join(
                f"{k} {d.get(k, 0) - pd.get(k, 0):+.3f}"
                for k in FLOATS if abs(d.get(k, 0) - pd.get(k, 0)) >= 1e-9)
            dps = []
            for k in PACKED:
                cur_b, pre_b = unpack(d.get(k, 0)), unpack(pd.get(k, 0))
                diff = [c - b for c, b in zip(cur_b, pre_b)]
                if any(diff):
                    mark = "v" if any(x < 0 for x in diff) else "+"
                    dps.append(f"{k}[{' '.join(f'{x:+d}' for x in diff)}]{mark}")
            dsc = " ".join(
                f"{k}{d.get(k, 0) - pd.get(k, 0):+,}" for k in SCALARS
                if d.get(k, 0) != pd.get(k, 0))
            bits = [b for b in (dfl.strip(), " ".join(dps), dsc.strip()) if b]
            if bits:
                print(f"    delta  : {' | '.join(bits)}")
        prev = (idx, d)
