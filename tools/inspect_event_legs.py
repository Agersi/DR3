#!/usr/bin/env python3
"""Dump one event's championship settings + every leg with per-stage service
labels (read-only). Used to verify DNF skip-plan inputs against what the web
actually stores.

Run on the VPS:  /opt/dr3/.venv/bin/python /tmp/inspect_event_legs.py EVENT_ID [EVENT_ID ...]
                 /opt/dr3/.venv/bin/python /tmp/inspect_event_legs.py --club CLUB_NAME_OR_ID
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

import pymysql  # noqa: E402

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             os.environ["DATABASE_URL"])
conn = pymysql.connect(host=p.group(3), port=int(p.group(4) or 3306),
                       user=p.group(1), password=p.group(2),
                       database=p.group(5).split("?")[0])
cur = conn.cursor()

args = sys.argv[1:]
if args and args[0] == "--club":
    needle = args[1] if len(args) > 1 else ""
    cur.execute("SELECT id FROM clubs WHERE name=%s OR id=%s", (needle, needle))
    row = cur.fetchone()
    if not row:
        raise SystemExit(f"no club matching {needle!r}")
    cur.execute("SELECT id FROM events WHERE club_id=%s ORDER BY start_time", (row[0],))
    args = [r[0] for r in cur.fetchall()]
    if not args:
        raise SystemExit(f"club {row[0]} has no events")

for eid in args:
    cur.execute("SELECT id, name, club_id, settings_json, legs_json FROM events WHERE id=%s",
                (eid,))
    row = cur.fetchone()
    if not row:
        print(f"{eid}: NOT FOUND")
        continue
    _id, name, club_id, settings, legs_raw = row
    print(f"== {eid} | {name} | club={club_id}")
    try:
        print("settings:", json.dumps(json.loads(settings or "{}")))
    except Exception:
        print("settings(raw):", (settings or "")[:300])
    legs = json.loads(legs_raw or "[]")
    for i, leg in enumerate(legs):
        print(f"--- leg {i}: location={leg.get('location')!r} "
              f"class={leg.get('car_class')!r} rx_laps={leg.get('rx_laps')!r}")
        for j, s in enumerate(leg.get("stages") or []):
            print(f"  stage {j}: track_id={s.get('track_id')} "
                  f"service_area={s.get('service_area')!r} name={s.get('name')!r}")
