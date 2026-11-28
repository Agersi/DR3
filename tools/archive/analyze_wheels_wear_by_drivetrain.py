#!/usr/bin/env python3
"""Aggregate comp_damage wheels_wear deltas by drivetrain and RX circuit.

The packed wheels_wear field is 4 x uint8 (little-endian byte order). This
tool scans club_stage_states, maps each flat stage ordinal to its leg
location/car class from the event definition, keeps only non-decreasing
wheel-to-wheel deltas inside one location segment (so tyre changes / service
resets and location changes are excluded), and aggregates the clean wear
increments. It also reports the first snapshot of every RX location segment,
since most RX legs have only one stage and therefore no usable delta.

Run on the VPS:
    /opt/dr3/.venv/bin/python analyze_wheels_wear_by_drivetrain.py [all|rally|rx] [EVENT_ID] [-v]
"""
import json
import os
import re
import sys
from collections import defaultdict

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import pymysql  # noqa: E402

RX_NAMES = {
    "Mettet", "Trois-Rivières", "Lydden Hill", "Silverstone", "Lohéac",
    "Estering", "Bikernieki", "Hell", "Montalegre", "Killarney",
    "Barcelona", "Höljes", "Yas Marina",
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

mode = sys.argv[1] if len(sys.argv) > 1 else "all"
event_filter = sys.argv[2] if len(sys.argv) > 2 else None
verbose = len(sys.argv) > 3 and sys.argv[3] == "-v"
if mode not in ("all", "rally", "rx"):
    sys.exit(f"unknown mode: {mode}")

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             os.environ["DATABASE_URL"])
conn = pymysql.connect(host=p.group(3), port=int(p.group(4) or 3306),
                       user=p.group(1), password=p.group(2),
                       database=p.group(5).split("?")[0])
cur = conn.cursor()

sql = """
SELECT css.event_id, css.username, css.flat_stage_index, css.comp_damage_json,
       e.location, e.car_class, e.stages_json, e.legs_json, cr.car
FROM club_stage_states css
JOIN events e ON e.id = css.event_id
LEFT JOIN club_results cr
  ON cr.event_id = css.event_id AND cr.username = css.username
WHERE css.comp_damage_json IS NOT NULL
"""
args = []
if event_filter:
    sql += " AND css.event_id = %s"
    args.append(event_filter)
sql += " ORDER BY css.event_id, css.username, css.flat_stage_index"
cur.execute(sql, args)


def unpack(v):
    return [(int(v or 0) >> (8 * i)) & 0xFF for i in range(4)]


def stage_plan(event_location, event_class, stages_json, legs_json):
    """Map flat stage ordinal -> (location, car_class label)."""
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
car_names = {}
total_rows = 0
for event_id, username, flat_idx, dam_json, ev_loc, ev_cls, stages_json, legs_json, car in cur.fetchall():
    total_rows += 1
    rows_by_event_user[(event_id, username)].append(
        (flat_idx, unpack(json.loads(dam_json).get("wheels_wear", 0))))
    plans.setdefault(event_id, stage_plan(ev_loc, ev_cls, stages_json, legs_json))
    if car:
        car_names[(event_id, username)] = car

rally_clean = defaultdict(lambda: [0] * 6)   # segs, stages, b0..b3 sums
rx_clean = defaultdict(lambda: [0] * 6)
rx_first_raw = defaultdict(lambda: [0] * 5)  # count, b0..b3 sums
rx_segments = []

for (event_id, username), rows in rows_by_event_user.items():
    plan = plans.get(event_id, [])
    rows.sort()
    prev = None
    prev_key = None
    acc = defaultdict(lambda: {"segs": 0, "stages": 0, "clean": [0, 0, 0, 0],
                               "first": None, "first_at_start": False,
                               "indices": []})

    for pos, (flat_idx, cur_b) in enumerate(rows):
        loc, cls = (plan[flat_idx] if flat_idx < len(plan) else ("?", "?"))
        dt = CLASS_DRIVETRAIN.get(cls, "?")
        key = (loc, dt)
        a = acc[key]
        a["stages"] += 1
        a["indices"].append(flat_idx)
        if a["first"] is None:
            a["first"] = cur_b[:]
            a["first_at_start"] = (pos == 0)
        if verbose:
            print(f"debug {event_id} {username} idx={flat_idx} key={key!r} "
                  f"bytes={cur_b}")

        if prev is not None and key == prev_key:
            delta = [c - p for c, p in zip(cur_b, prev)]
            if verbose:
                print(f"debug delta idx={flat_idx} {delta} clean={a['clean']}")
            if all(x >= 0 for x in delta) and any(delta):
                a["segs"] += 1
                for i in range(4):
                    a["clean"][i] += delta[i]
        prev = cur_b
        prev_key = key

    for (loc, dt), a in acc.items():
        if loc in RX_NAMES:
            if a["first"] is not None:
                b = rx_first_raw[loc]
                b[0] += 1
                for i in range(4):
                    b[i + 1] += a["first"][i]
            rx_segments.append({
                "event": event_id,
                "user": username,
                "car": car_names.get((event_id, username), "?"),
                "loc": loc,
                "class": dt,
                "indices": a["indices"],
                "first_at_start": a["first_at_start"],
                "first": a["first"] or [0, 0, 0, 0],
                "segs": a["segs"],
                "clean": a["clean"],
            })
            if a["segs"]:
                bucket = rx_clean[(loc, dt)]
                bucket[0] += a["segs"]
                bucket[1] += a["stages"]
                for i in range(4):
                    bucket[i + 2] += a["clean"][i]
        else:
            if a["segs"]:
                bucket = rally_clean[dt]
                bucket[0] += a["segs"]
                bucket[1] += a["stages"]
                for i in range(4):
                    bucket[i + 2] += a["clean"][i]


def show(name, b):
    # Assumed byte order from the 2026-11-16 deliberate-hit test:
    # [RL, RR, FL, FR] — front = b2+b3, rear = b0+b1, left = b0+b2, right = b1+b3.
    front = b[4] + b[5]
    rear = b[2] + b[3]
    left = b[2] + b[4]
    right = b[3] + b[5]
    print(f"{name:35s} n={b[0]:4d} stages={b[1]:4d}  "
          f"b0={b[2]:6d} b1={b[3]:6d} b2={b[4]:6d} b3={b[5]:6d}  "
          f"F={front:6d} R={rear:6d} L={left:6d} Rt={right:6d}")


print(f"scanned {total_rows} club_stage_states rows across "
      f"{len(rows_by_event_user)} event/driver series\n")

if mode in ("all", "rally"):
    print("=== Rally — clean inter-stage deltas by drivetrain ===")
    for dt in ("FWD", "RWD", "4WD", "?"):
        if dt in rally_clean:
            show(f"rally {dt}", rally_clean[dt])

if mode in ("all", "rx"):
    print("\n=== RX — clean inter-stage deltas by circuit ===")
    for (loc, dt), b in sorted(rx_clean.items(), key=lambda kv: (-kv[1][0], kv[0])):
        show(f"{loc} {dt}", b)

    print("\n=== RX — first snapshot of each RX location segment ===")
    for loc, b in sorted(rx_first_raw.items(), key=lambda kv: -kv[1][0]):
        front = b[3] + b[4]
        rear = b[1] + b[2]
        left = b[1] + b[3]
        right = b[2] + b[4]
        print(f"{loc:20s} n={b[0]:3d}  "
              f"b0={b[1]:5d} b1={b[2]:5d} b2={b[3]:5d} b3={b[4]:5d}  "
              f"F={front:5d} R={rear:5d} L={left:5d} Rt={right:5d}")

    print("\n=== RX segment detail ===")
    rx_segments.sort(key=lambda s: (s["loc"], s["event"], s["user"]))
    for s in rx_segments[:100]:
        c = s["first"]
        front = c[2] + c[3]
        rear = c[0] + c[1]
        left = c[0] + c[2]
        right = c[1] + c[3]
        start_tag = "start" if s["first_at_start"] else "mid-ev"
        print(f"{s['event']} {s['user']:20s} {s['loc']:15s} {s['class']:5s} "
              f"idx={s['indices']} {start_tag} raw=[{c[0]:3d} {c[1]:3d} {c[2]:3d} {c[3]:3d}] "
              f"F={front:3d} R={rear:3d} L={left:3d} Rt={right:3d}  car={s['car']}")
