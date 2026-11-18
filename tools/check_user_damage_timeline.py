#!/usr/bin/env python3
"""Per-stage comp_damage timeline for one user across all their events.

For a given username, walks every club_stage_states row and prints the packed
wheel fields (wheels_wear / wheels_impact) unpacked byte-wise plus quick_repairs,
ordered by event then flat stage index. Used to spot where a high wheel value
(puncture signature) drops back to low on the following stage — i.e. the
"exit club and rejoin repairs the puncture" transition.

Run on the VPS:
    /opt/dr3/.venv/bin/python check_user_damage_timeline.py USERNAME
"""
import json
import os
import re
import sys

if len(sys.argv) < 2:
    sys.exit("usage: check_user_damage_timeline.py USERNAME")
username = sys.argv[1]

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

cur.execute("""
    SELECT event_id, flat_stage_index, comp_damage_json
    FROM club_stage_states
    WHERE username=%s AND comp_damage_json IS NOT NULL
    ORDER BY event_id, flat_stage_index
""", (username,))
rows = cur.fetchall()

def unpack(v):
    return [(v >> (8 * i)) & 0xFF for i in range(4)]

ids = tuple({r[0] for r in rows})
if ids:
    cur.execute("SELECT id, name FROM events WHERE id IN %s", (ids,))
    names = {e: n for e, n in cur.fetchall()}
else:
    names = {}

by_event = {}
for event_id, idx, dam in rows:
    by_event.setdefault(event_id, []).append((idx, json.loads(dam)))

for event_id in sorted(by_event):
    print(f"\n=== {event_id} ({names.get(event_id, '?')}) ===")
    prev = None
    for idx, d in sorted(by_event[event_id]):
        ww = unpack(d.get("wheels_wear", 0) or 0)
        wi = unpack(d.get("wheels_impact", 0) or 0)
        qr = int(d.get("quick_repairs", 0) or 0)
        flag = ""
        if prev is not None:
            pwi = prev[1]
            drops = [k for k in range(4) if wi[k] < pwi[k] - 5]
            if drops:
                flag = "  <== impact drop on b" + "/b".join(str(k) for k in drops)
        print(f"stage {idx}: wear=[{ww[3]:3d} {ww[2]:3d} {ww[1]:3d} {ww[0]:3d}] "
              f"impact=[{wi[3]:3d} {wi[2]:3d} {wi[1]:3d} {wi[0]:3d}] qr={qr}{flag}")
        prev = (idx, wi)
