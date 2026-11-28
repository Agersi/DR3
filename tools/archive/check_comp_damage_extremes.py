#!/usr/bin/env python3
"""Scan stored comp_damage for extreme values in the packed 4xuint8 fields.

Walks every club_stage_states.comp_damage_json row, unpacks the four wheel
bytes of each requested packed field (wheels_wear / wheels_impact / springs /
brakes / dampers / bodywork), and reports per-wheel max/mean plus a listing of
rows with any wheel >= --threshold. Used to hunt for on-the-wire signatures of
game state (e.g. whether a punctured tyre shows up as extreme wheel impact).

Run on the VPS:
    /opt/dr3/.venv/bin/python check_comp_damage_extremes.py [FIELD ...] \
        [--threshold N] [--top N]

Default fields: wheels_impact wheels_wear
"""
import json
import os
import re
import sys

FIELDS = ("wheels_wear", "wheels_impact", "springs", "brakes",
          "dampers", "bodywork")
args = [a for a in sys.argv[1:]]
threshold = 240
top = 40
fields = []
i = 0
while i < len(args):
    a = args[i]
    if a == "--threshold":
        threshold = int(args[i + 1]); i += 2; continue
    if a == "--top":
        top = int(args[i + 1]); i += 2; continue
    fields.append(a); i += 1
if not fields:
    fields = ["wheels_impact", "wheels_wear"]
fields = [f for f in fields if f in FIELDS] or list(FIELDS)

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
""")
rows = cur.fetchall()

total = 0
per_field = {f: {"max": [0, 0, 0, 0], "sum": [0, 0, 0, 0]} for f in fields}
extremes = {f: [] for f in fields}
for event_id, username, idx, dam in rows:
    try:
        d = json.loads(dam)
    except (ValueError, TypeError):
        continue
    total += 1
    for f in fields:
        val = d.get(f, 0) or 0
        b = [(val >> (8 * k)) & 0xFF for k in range(4)]
        per_field[f]["max"] = [max(per_field[f]["max"][k], b[k]) for k in range(4)]
        per_field[f]["sum"] = [per_field[f]["sum"][k] + b[k] for k in range(4)]
        if any(x >= threshold for x in b):
            extremes[f].append((max(b), event_id, username, idx, b))

print(f"rows with comp_damage: {total}")
for f in fields:
    mx = per_field[f]["max"]
    sm = per_field[f]["sum"]
    means = [f"{sm[k] / total:.1f}" if total else "0" for k in range(4)]
    print(f"\n=== {f} ===")
    for k in range(4):
        print(f"  b{k}: max={mx[k]:3d} mean={means[k]}")
    hits = extremes[f]
    print(f"  rows with any wheel >= {threshold}: {len(hits)}")
    for m, ev, u, idx, b in sorted(hits, reverse=True)[:top]:
        print(f"    max={m:3d} {ev} {u} stage{idx} bytes={b}")
