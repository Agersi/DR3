#!/usr/bin/env python3
"""Align packed comp_damage fields against the confirmed wheels_wear order.

wheels_wear is confirmed as [RL, RR, FL, FR]. For every other packed field this
tool scores all 24 byte orders by placing that field's bytes into corner slots
and measuring how well the per-corner profile tracks the wheels_wear profile
(mean-centred dot product across every stored stage snapshot).

A high score for [RL, RR, FL, FR] means the field uses the same corner layout.
A different winner means the field stores its four values in a different order.

Run on the VPS:
    /opt/dr3/.venv/bin/python align_comp_damage_wheel_fields.py [FIELD|all]
"""
import json
import os
import re
import sys
from collections import defaultdict
from itertools import permutations

PACKED = ("brakes", "dampers", "springs", "wheels_wear", "wheels_impact", "bodywork")

args = [a for a in sys.argv[1:] if a != "-v"]
field_arg = args[0] if args else "all"
if field_arg == "all":
    fields = [f for f in PACKED if f != "wheels_wear"]
elif field_arg in PACKED and field_arg != "wheels_wear":
    fields = [field_arg]
else:
    sys.exit(f"unknown field: {field_arg}")

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

sql = "SELECT comp_damage_json FROM club_stage_states WHERE comp_damage_json IS NOT NULL"
cur.execute(sql)


def unpack(v):
    return [(int(v or 0) >> (8 * i)) & 0xFF for i in range(4)]


CORNERS = ("FL", "FR", "RL", "RR")
# confirmed wheels_wear byte order: b0=RL b1=RR b2=FL b3=FR
BASE_CB = {"RL": 0, "RR": 1, "FL": 2, "FR": 3}
CONFIRMED = ("RL", "RR", "FL", "FR")

CANDIDATES = {
    "W_rear_first": ("RL", "RR", "FL", "FR"),
    "A_front_first": ("FL", "FR", "RL", "RR"),
    "B_cyclic_fl": ("FL", "RL", "RR", "FR"),
    "C_side_grouped": ("FR", "RR", "FL", "RL"),
}

PERMS = []
for idx, perm in enumerate(permutations(range(4))):
    cb = dict(zip(CORNERS, perm))
    byte_order = [None] * 4
    for corner, bi in cb.items():
        byte_order[bi] = corner
    PERMS.append((idx, tuple(byte_order), cb))

samples = []
for (dam_json,) in cur.fetchall():
    data = json.loads(dam_json)
    base = unpack(data.get("wheels_wear", 0))
    if not any(base):
        continue
    sample = {"base": base}
    for f in fields:
        sample[f] = unpack(data.get(f, 0))
    samples.append(sample)


def centered_dot(a, b):
    ma = sum(a) / 4
    mb = sum(b) / 4
    return sum((x - ma) * (y - mb) for x, y in zip(a, b))


print(f"samples with nonzero wheels_wear: {len(samples)}")
for field in fields:
    scores = []
    for idx, byte_order, cb in PERMS:
        score = 0
        for s in samples:
            base_corner = [s["base"][BASE_CB[c]] for c in CORNERS]
            field_corner = [s[field][cb[c]] for c in CORNERS]
            score += centered_dot(base_corner, field_corner)
        scores.append((score, idx, byte_order))
    scores.sort(key=lambda x: (x[0], -x[1]), reverse=True)

    print(f"\n########## {field} vs wheels_wear ##########")
    print("Top alignments (positive = high bytes land on the same corners):")
    for score, _idx, byte_order in scores[:8]:
        mark = "  <== confirmed wheels_wear order" if byte_order == CONFIRMED else ""
        print(f"[{', '.join(byte_order):25s}] {score:+10.1f}{mark}")
    conf_score = next(s for s, _i, b in scores if b == CONFIRMED)
    conf_rank = next(i for i, (s, _x, b) in enumerate(scores) if b == CONFIRMED) + 1
    print(f"confirmed [RL,RR,FL,FR]: score {conf_score:+.1f}, rank {conf_rank}/24")
    for name, order in CANDIDATES.items():
        try:
            s = next(x for x, _i, b in scores if b == order)
            r = next(i for i, (x, _j, b) in enumerate(scores) if b == order) + 1
            print(f"candidate {name} [{', '.join(order)}]: score {s:+.1f}, rank {r}/24")
        except StopIteration:
            pass
