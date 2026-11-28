#!/usr/bin/env python3
"""Scan stored comp_damage for extreme WheelsWear values (puncture signature hunt).

Walks every club_stage_states.comp_damage_json row, unpacks the 4 wheel bytes
of wheels_wear, and reports: total rows, per-wheel max/mean, rows with any
wheel >= --threshold (default 240), and a listing of the top extreme rows
(event, user, stage, byte values). Used to determine whether a punctured tyre
is ever visible on the wire as extreme wheel wear.

Run on the VPS:
    /opt/dr3/.venv/bin/python check_wheels_wear_extremes.py [--threshold N]
"""
import json
import os
import re
import sys

THRESHOLD = 240
if "--threshold" in sys.argv:
    i = sys.argv.index("--threshold")
    THRESHOLD = int(sys.argv[i + 1])

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
    SELECT s.event_id, s.username, s.flat_stage_index, s.comp_damage_json
    FROM club_stage_states s
    WHERE s.comp_damage_json IS NOT NULL
""")
rows = cur.fetchall()

per_wheel_max = [0, 0, 0, 0]
per_wheel_sum = [0, 0, 0, 0]
extremes = []
total = 0
for event_id, username, idx, dam in rows:
    try:
        d = json.loads(dam)
    except (ValueError, TypeError):
        continue
    total += 1
    ww = d.get("wheels_wear", 0)
    b = [(ww >> (8 * i)) & 0xFF for i in range(4)]
    for i in range(4):
        per_wheel_max[i] = max(per_wheel_max[i], b[i])
        per_wheel_sum[i] += b[i]
    if any(x >= THRESHOLD for x in b):
        extremes.append((max(b), event_id, username, idx, b))

print(f"rows with comp_damage: {total}")
for i, name in enumerate(("b0(RL?)", "b1(RR?)", "b2(FL?)", "b3(FR?)")):
    if total:
        print(f"  {name}: max={per_wheel_max[i]} mean={per_wheel_sum[i] / total:.1f}")
print(f"rows with any wheel >= {THRESHOLD}: {len(extremes)}")
for m, ev, u, idx, b in sorted(extremes, reverse=True)[:40]:
    print(f"  max={m:3d} {ev} {u} stage{idx} bytes={b}")
