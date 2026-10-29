#!/usr/bin/env python3
"""Backfill vehicle data into DNF result rows that predate the fix which
passes the close-out payload's VehicleId/LiveryId/NationalityId through to
the web (service-area retirements capture no stage-begin, so those rows are
entirely vehicle-less: blank car/class/flag on leaderboards and a
VehicleId=0/LiveryId=0 next-leg carry-over that crashes the game on Start).

Usage (on the VPS):
  /opt/dr3/.venv/bin/python /tmp/backfill_dnf_vehicle.py \
      RESULT_ID VEHICLE_ID LIVERY_ID [NATIONALITY_ID] [STAGE_POS ...]

With no STAGE_POS, every stage entry lacking a vehicle_id is filled. The
Result-level vehicle_id/car columns are always set (leaderboards read the
column). Idempotent: entries that already carry a different vehicle keep it.
"""
import json
import os
import sys

sys.path.insert(0, "/opt/dr3")

import pymysql  # noqa: E402
from dr2server.game_data import VEHICLES  # noqa: E402


def car_label_for(vid):
    meta = VEHICLES.get(int(vid))
    if meta:
        return str(meta["name"])
    return f"Vehicle {int(vid)}"


if len(sys.argv) < 5:
    print(__doc__)
    raise SystemExit(1)

rid = int(sys.argv[1])
vid = int(sys.argv[2])
liv = int(sys.argv[3])
nat = int(sys.argv[4]) if len(sys.argv) > 4 else None
positions = [int(a) for a in sys.argv[5:]] or None

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import re  # noqa: E402

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             os.environ["DATABASE_URL"])
user, pw, host, port, dbname = (p.group(1), p.group(2), p.group(3),
                                int(p.group(4) or 3306), p.group(5).split("?")[0])
conn = pymysql.connect(host=host, port=port, user=user, password=pw, database=dbname)
cur = conn.cursor()
cur.execute("SELECT event_id, username, stages_json FROM results WHERE id=%s", (rid,))
row = cur.fetchone()
if not row:
    raise SystemExit(f"result {rid} not found")
eid, username, sj = row
stages = json.loads(sj or "[]")

changed = 0
for i, s in enumerate(stages):
    if positions is not None and i not in positions:
        continue
    if not s or s.get("vehicle_id"):
        continue
    s["vehicle_id"] = vid
    s["livery_id"] = liv
    if nat:
        s["nationality_id"] = nat
    changed += 1

cur.execute(
    "UPDATE results SET stages_json=%s, vehicle_id=%s, car=%s WHERE id=%s",
    (json.dumps(stages), vid, car_label_for(vid), rid),
)
conn.commit()
print(f"result {rid} ({eid} / {username}): filled {changed} stage entr"
      f"{'y' if changed == 1 else 'ies'} with vehicle={vid} livery={liv}"
      + (f" nationality={nat}" if nat else ""))
print(f"result-level vehicle_id -> {vid} ({car_label_for(vid)})")
conn.close()
