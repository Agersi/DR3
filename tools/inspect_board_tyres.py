#!/usr/bin/env python3
"""Dump the per-stage tyre fields the overall board reads, leg by leg.

For one club event it prints the stage layout (stages per leg) and, for each
participant (or one USERNAME), every flat stage entry with its leg number,
time_ms / race_status, the stored tyre_compound + tyres_remaining, and the
matching stage_runs row's tyre compound. Used to debug the overall-mode tyre
sequence on multi-location rallies.

Run on the VPS:
    /opt/dr3/.venv/bin/python inspect_board_tyres.py EVENT_ID [USERNAME]
"""
import json
import os
import re
import sys

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, "/opt/dr3/web")

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
    sys.exit("usage: inspect_board_tyres.py EVENT_ID [USERNAME]")

cur.execute(
    "SELECT id, name, settings_json, legs_json FROM events WHERE id=%s",
    (event_id,))
row = cur.fetchone()
if not row:
    sys.exit(f"unknown event: {event_id}")
eid, name, settings_json, legs_json = row
legs = json.loads(legs_json or '[]')
layout = [len(ev.get('stages', []) or []) for ev in legs]
offsets = []
acc = 0
for c in layout:
    offsets.append(acc)
    acc += c
print(f"event: {eid} \"{name}\"")
print(f"legs: {layout} (total {acc} stages)")
for i, ev in enumerate(legs):
    print(f"   leg {i+1}: {len(ev.get('stages', []) or [])} stages "
          f"offset={offsets[i]}")


def leg_of(flat: int) -> int:
    for i, off in enumerate(offsets):
        if flat < off + layout[i]:
            return i + 1
    return len(legs) + 1


cur.execute(
    "SELECT username, stages_json FROM club_results WHERE event_id=%s",
    (eid,))
results = cur.fetchall()

cur.execute(
    "SELECT username, flat_stage_index, tyre_compound FROM stage_runs "
    "WHERE event_id=%s ORDER BY username, flat_stage_index", (eid,))
runs = {}
for u, fsi, tc in cur.fetchall():
    runs.setdefault(u, {})[fsi] = tc

users = [u for u, _ in results]
if user_filter:
    users = [u for u in users if u == user_filter]
    if not users:
        sys.exit(f"no result row for {user_filter} in {eid}")

for u in sorted(users):
    stages = json.loads(dict(results)[u] or '[]')
    print(f"\n== {u}:")
    for i, s in enumerate(stages):
        if not isinstance(s, dict):
            print(f"   flat={i} leg{leg_of(i)}: <missing>")
            continue
        t = int(s.get('time_ms') or 0)
        rs = int(s.get('race_status', 0) or 0)
        tc = s.get('tyre_compound')
        tr = s.get('tyres_remaining')
        run_tc = runs.get(u, {}).get(i)
        dnf = ' DNF' if rs else ''
        print(f"   flat={i} leg{leg_of(i)}: time={t}ms rs={rs}{dnf} "
              f"entry_tyre={tc} rem={tr} run_tyre={run_tc}")
