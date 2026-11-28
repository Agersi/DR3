#!/usr/bin/env python3
"""Score all 24 corner byte orders for packed comp_damage fields.

For every field this tool aggregates the same clean delta data as
analyze_comp_damage_corner_order.py, then scores all 24 permutations of
[FL, FR, RL, RR] against:

- RX circuit direction (clockwise => left side should accumulate more,
  counter-clockwise => right side), using entry + in-segment deltas,
- the same signal computed from raw first snapshots (weaker, carried damage
  can leak in),
- rally drivetrain front/rear sums (display only; the physical expectation
  differs per field).

Run on the VPS:
    /opt/dr3/.venv/bin/python score_comp_damage_corner_orders.py [FIELD|all] [-v]
"""
import json
import os
import re
import sys
from collections import defaultdict
from itertools import permutations

PACKED = ("brakes", "dampers", "springs", "wheels_wear", "wheels_impact", "bodywork")

RX_NAMES = {
    "Mettet", "Trois-Rivières", "Lydden Hill", "Silverstone", "Lohéac",
    "Estering", "Bikernieki", "Hell", "Montalegre", "Killarney",
    "Barcelona", "Höljes", "Yas Marina",
}

RX_DIRECTION = {
    "Hell": "CW",
    "Estering": "CW",
    "Killarney": "CW",
    "Trois-Rivières": "CW",
    "Höljes": "CCW",
}

CLASS_DRIVETRAIN = {
    "H1 (FWD)": "FWD", "H2 (FWD)": "FWD", "R2": "FWD",
    "F2 Kit Car": "FWD", "RX Super 1600": "FWD",
    "H2 (RWD)": "RWD", "H3 (RWD)": "RWD", "Group B (RWD)": "RWD",
    "Rally GT": "RWD", "Cross Kart": "RWD",
    "Group A": "4WD", "Group B (4WD)": "4WD", "R5": "4WD",
    "NR4/R4": "4WD", "2000cc": "4WD", "Group B Rallycross": "4WD",
    "RX2": "4WD", "RX Supercars": "4WD", "RX Supercars 2019": "4WD",
}

args = [a for a in sys.argv[1:] if a != "-v"]
field_arg = args[0] if args else "all"
if field_arg == "all":
    fields = PACKED
elif field_arg in PACKED:
    fields = (field_arg,)
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

sql = """
SELECT css.event_id, css.username, css.flat_stage_index, css.comp_damage_json,
       e.location, e.car_class, e.stages_json, e.legs_json
FROM club_stage_states css
JOIN events e ON e.id = css.event_id
WHERE css.comp_damage_json IS NOT NULL
ORDER BY css.event_id, css.username, css.flat_stage_index
"""
cur.execute(sql)


def unpack(v):
    return [(int(v or 0) >> (8 * i)) & 0xFF for i in range(4)]


def stage_plan(event_location, event_class, stages_json, legs_json):
    try:
        legs = json.loads(legs_json or "[]")
    except ValueError:
        legs = []
    plan = []
    for leg in legs:
        loc = (leg.get("location") or "").strip()
        cls = (leg.get("car_class") or "").strip()
        for _ in (leg.get("stages") or []):
            plan.append((loc, cls))
    if not plan:
        try:
            stages = json.loads(stages_json or "[]")
        except ValueError:
            stages = []
        loc = (event_location or "").strip()
        cls = (event_class or "").strip()
        plan = [(loc, cls) for _ in stages]
    return plan


rows_by_event_user = defaultdict(list)
plans = {}
total_rows = 0
for event_id, username, flat_idx, dam_json, ev_loc, ev_cls, stages_json, legs_json in cur.fetchall():
    total_rows += 1
    data = json.loads(dam_json)
    rows_by_event_user[(event_id, username)].append(
        (flat_idx, {f: unpack(data.get(f, 0)) for f in fields}))
    plans.setdefault(event_id, stage_plan(ev_loc, ev_cls, stages_json, legs_json))

CORNERS = ("FL", "FR", "RL", "RR")
PERMS = []
for perm in permutations(range(4)):
    cb = dict(zip(CORNERS, perm))  # corner -> byte index
    byte_order = [None] * 4
    for corner, bi in cb.items():
        byte_order[bi] = corner
    PERMS.append((tuple(byte_order), cb))

CONFIRMED = ("RL", "RR", "FL", "FR")


def side_diff(cb, b):
    # b layout: [count, byte0, byte1, byte2, byte3]
    left = b[1 + cb["FL"]] + b[1 + cb["RL"]]
    right = b[1 + cb["FR"]] + b[1 + cb["RR"]]
    return left - right


def score_rx(rx_data, cb):
    matches = wrong = 0
    weighted = 0
    total = 0
    for loc, b in rx_data.items():
        d = RX_DIRECTION.get(loc)
        if not d or not b[0]:
            continue
        diff = side_diff(cb, b)
        expected = 1 if d == "CW" else -1
        weighted += diff * expected
        total += b[0]
        if diff * expected > 0:
            matches += 1
        elif diff * expected < 0:
            wrong += 1
    return matches, wrong, weighted, total


def axle_diff(cb, b):
    # b layout: [segs, stages, byte0..byte3]
    front = b[2 + cb["FL"]] + b[2 + cb["FR"]]
    rear = b[2 + cb["RL"]] + b[2 + cb["RR"]]
    return front - rear


for field in fields:
    rally_clean = defaultdict(lambda: [0] * 6)
    rx_delta = defaultdict(lambda: [0] * 5)
    rx_snapshot = defaultdict(lambda: [0] * 5)

    for (event_id, username), rows in rows_by_event_user.items():
        plan = plans.get(event_id, [])
        rows.sort()
        prev = None
        prev_key = None
        seen_rx_loc = set()

        for pos, (flat_idx, vals) in enumerate(rows):
            cur_b = vals[field]
            loc, cls = (plan[flat_idx] if flat_idx < len(plan) else ("?", "?"))
            dt = CLASS_DRIVETRAIN.get(cls, "?")
            key = (loc, dt)

            if prev is not None:
                delta = [c - p for c, p in zip(cur_b, prev)]
                clean = all(x >= 0 for x in delta) and any(delta)
                if clean:
                    if key == prev_key:
                        bucket = rally_clean[dt] if loc not in RX_NAMES else None
                        if bucket is not None:
                            bucket[0] += 1
                            for i in range(4):
                                bucket[i + 2] += delta[i]
                    if loc in RX_NAMES:
                        b = rx_delta[loc]
                        b[0] += 1
                        for i in range(4):
                            b[i + 1] += delta[i]

            if loc in RX_NAMES and loc not in seen_rx_loc:
                seen_rx_loc.add(loc)
                b = rx_snapshot[loc]
                b[0] += 1
                for i in range(4):
                    b[i + 1] += cur_b[i]

            prev = cur_b
            prev_key = key

    print(f"\n########## {field} ##########")
    print("RX circuits with clean deltas:",
          ", ".join(f"{loc}({RX_DIRECTION.get(loc, '?')},n={b[0]})"
                    for loc, b in sorted(rx_delta.items()) if b[0]) or "none")

    scored = []
    for idx, (byte_order, cb) in enumerate(PERMS):
        m1, w1, weight1, tot1 = score_rx(rx_delta, cb)
        m2, w2, weight2, tot2 = score_rx(rx_snapshot, cb)
        scored.append((m1, -w1, weight1, m2, -w2, weight2, idx, byte_order, cb))
    scored.sort(key=lambda s: s[:6], reverse=True)

    print("\nTop byte orders by RX direction (delta score, then snapshot score):")
    print(f"{'byte order':28s} {'rx_delta':10s} {'weighted':>9s} {'rx_snap':10s} {'weighted':>9s}  "
          f"rally F-R: FWD/RWD/4WD")
    for m1, nw1, weight1, m2, nw2, weight2, _idx, byte_order, cb in scored[:8]:
        w1 = -nw1
        w2 = -nw2
        fwd = axle_diff(cb, rally_clean["FWD"]) if "FWD" in rally_clean else None
        rwd = axle_diff(cb, rally_clean["RWD"]) if "RWD" in rally_clean else None
        awd = axle_diff(cb, rally_clean["4WD"]) if "4WD" in rally_clean else None
        f = lambda x: "-" if x is None else f"{x:+d}"
        mark = "  <== confirmed wheels_wear order" if byte_order == CONFIRMED else ""
        print(f"[{', '.join(byte_order):25s}] {m1}/? ok {w1}wrong {weight1:+9d} "
              f"{m2}/? ok {w2}wrong {weight2:+9d}  {f(fwd)}/{f(rwd)}/{f(awd)}{mark}")

    conf_cb = dict(zip(CORNERS, (2, 3, 0, 1)))  # FL=2 FR=3 RL=0 RR=1
    m1, w1, weight1, tot1 = score_rx(rx_delta, conf_cb)
    m2, w2, weight2, tot2 = score_rx(rx_snapshot, conf_cb)
    rank = next(i for i, s in enumerate(scored) if s[7] == CONFIRMED) + 1
    print(f"\nconfirmed [RL,RR,FL,FR]: RX-delta {m1} ok / {w1} wrong (weighted {weight1:+d}, n={tot1}); "
          f"snapshot {m2} ok / {w2} wrong (weighted {weight2:+d}, n={tot2}); rank {rank}/24")

print(f"scanned {total_rows} club_stage_states rows", file=sys.stderr)
