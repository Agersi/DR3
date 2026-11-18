#!/usr/bin/env python3
"""Show full comp_damage for high-wheels_impact stages plus neighbours.

Finds club_stage_states rows whose wheels_impact has any wheel >= threshold,
then prints that row and the previous/next stage (same user+event) with all
six packed fields unpacked, so puncture signatures (and whether they persist
into the following stage's stored damage) can be inspected.

Run on the VPS:
    /opt/dr3/.venv/bin/python check_high_impact_context.py [--threshold N]
"""
import json
import os
import re
import sys

THRESHOLD = 180
if "--threshold" in sys.argv:
    i = sys.argv.index("--threshold")
    THRESHOLD = int(sys.argv[i + 1])

PACKED = ("wheels_wear", "wheels_impact", "springs", "brakes",
          "dampers", "bodywork")
FLOATS = ("clutch", "engine", "exhaust", "lights", "radiator")
SCALARS = ("gearbox", "turbo", "diff_wear", "diff_impact", "quick_repairs")

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
    SELECT event_id, username, flat_stage_index, comp_damage_json
    FROM club_stage_states
    WHERE comp_damage_json IS NOT NULL
    ORDER BY event_id, username, flat_stage_index
""")
by_key = {}
high = []
for event_id, username, idx, dam in cur.fetchall():
    try:
        d = json.loads(dam)
    except (ValueError, TypeError):
        continue
    by_key.setdefault((event_id, username), {})[idx] = d
    wi = d.get("wheels_impact", 0) or 0
    b = [(wi >> (8 * k)) & 0xFF for k in range(4)]
    if max(b) >= THRESHOLD:
        high.append((event_id, username, idx))

def unpack(v):
    return [(v >> (8 * i)) & 0xFF for i in range(4)]

def fmt(d):
    out = []
    for k in PACKED:
        out.append(f"{k}=[{' '.join(f'{x:3d}' for x in unpack(d.get(k, 0) or 0))}]")
    out.append(" ".join(f"{k}={int(d.get(k, 0) or 0):,}" for k in SCALARS))
    return " | ".join(out)

for event_id, username, idx in high:
    stages = by_key[(event_id, username)]
    print(f"\n=== {event_id} / {username} — impact spike at stage {idx} ===")
    for j in (idx - 1, idx, idx + 1):
        if j not in stages:
            continue
        tag = "  <== HIGH" if j == idx else ""
        print(f"stage {j}: {fmt(stages[j])}{tag}")
