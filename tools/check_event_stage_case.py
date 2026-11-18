#!/usr/bin/env python3
"""Inspect one club event's settings + a user's stored stage entries.

Shows the event name/type/settings/legs (to see hardcore flag and service
placement) and the user's per-stage result rows (times, race_status,
vehicle_damage, spare info) so a specific stage transition can be examined.

Run on the VPS:
    /opt/dr3/.venv/bin/python check_event_stage_case.py EVENT_ID [USERNAME]
"""
import json
import os
import re
import sys

if len(sys.argv) < 2:
    sys.exit("usage: check_event_stage_case.py EVENT_ID [USERNAME]")
event_id = sys.argv[1]
username = sys.argv[2] if len(sys.argv) > 2 else None

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

cur.execute("SELECT name, type, settings_json, legs_json, start_time, end_time"
            " FROM events WHERE id=%s", (event_id,))
r = cur.fetchone()
if not r:
    sys.exit(f"event {event_id} not found")
print(f"EVENT: {r[0]}  type={r[1]}  start={r[4]} end={r[5]}")
settings = json.loads(r[2])
print("SETTINGS:")
for k, v in settings.items():
    print(f"  {k}: {json.dumps(v)}")
legs = json.loads(r[3])
print(f"LEGS ({len(legs)}):")
for i, leg in enumerate(legs):
    st = leg.get("stages", [])
    svc = [s.get("stage_index") for s in st if s.get("is_service")]
    print(f"  leg {i}: {len(st)} stages service_at={svc}")

if username:
    cur.execute("SELECT stages_json, in_progress_json FROM club_results"
                " WHERE event_id=%s AND username=%s", (event_id, username))
    r2 = cur.fetchone()
    if not r2:
        sys.exit(f"no result for {username} in {event_id}")
    print(f"\nRESULT for {username}:")
    stages = json.loads(r2[0])
    for i, s in enumerate(stages):
        if not s:
            print(f"stage {i}: (empty)")
            continue
        keep = {k: v for k, v in s.items()
                if k in ("time_ms", "race_status", "vehicle_id", "spare_tyres",
                         "tyre_compound", "has_repaired", "penalty_ms")}
        vd = s.get("vehicle_damage")
        print(f"stage {i}: {json.dumps(keep)}"
              + (f" damage={json.dumps(vd)}" if vd else ""))
    ip = json.loads(r2[1] or "{}")
    if ip:
        print(f"in_progress: {json.dumps(ip)}")
