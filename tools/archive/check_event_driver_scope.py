#!/usr/bin/env python3
"""Audit the dashboard "Drivers" column against production data (read-only).

For each event, recomputes exactly what dashboard() computes — current window
scope, per-driver finished/closed/touched within that scope (closed = genuine
finish OR terminal DNF), dot color and done/total — and prints it next to the
raw stage entries, so layout mismatches and DNF race_status flags are visible
at a glance.

Run on the VPS:
    /opt/dr3/.venv/bin/python check_event_driver_scope.py EVENT_ID [EVENT_ID ...]
    /opt/dr3/.venv/bin/python check_event_driver_scope.py --club CLUB_NAME_OR_ID
Add --user USERNAME to flag one driver's row (e.g. the signed-in user).
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import pymysql  # noqa: E402

sys.path.insert(0, "/opt/dr3/web")
from app.results_view import (  # noqa: E402
    current_event_scope, effective_end, mode_of, scope_participation)

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             os.environ["DATABASE_URL"])
conn = pymysql.connect(host=p.group(3), port=int(p.group(4) or 3306),
                       user=p.group(1), password=p.group(2),
                       database=p.group(5).split("?")[0])
cur = conn.cursor()

args = sys.argv[1:]
user_filter = None
if "--user" in args:
    i = args.index("--user")
    user_filter = args[i + 1]
    del args[i:i + 2]
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

now = datetime.now(timezone.utc).replace(tzinfo=None)

for eid in args:
    cur.execute("""SELECT id, name, club_id, location, car_class, type, stages_json,
                          settings_json, legs_json, start_time, end_time
                   FROM events WHERE id=%s""", (eid,))
    row = cur.fetchone()
    if not row:
        print(f"{eid}: NOT FOUND")
        continue
    (_id, name, club_id, location, car_class, etype, stages_raw,
     settings_raw, legs_raw, start_time, end_time) = row
    event = {
        'id': _id, 'name': name, 'club_id': club_id, 'location': location,
        'car_class': car_class, 'type': etype,
        'stages': json.loads(stages_raw or '[]'),
        'settings': json.loads(settings_raw or '{}'),
        'events': json.loads(legs_raw or '[]'),
        'start_time': start_time.isoformat() if start_time else None,
        'end_time': end_time.isoformat() if end_time else None,
    }
    offset, count = current_event_scope(event, now)
    cur.execute("SELECT COUNT(*) FROM club_members WHERE club_id=%s", (club_id,))
    members = cur.fetchone()[0]
    print(f"== {eid} | {name} | mode={mode_of(event)} "
          f"| start={start_time} end={end_time}")
    print(f"   scope=({offset},{count}) current_end={effective_end(event, now)} "
          f"club_members={members}")
    cur.execute("SELECT username, stages_json FROM club_results WHERE event_id=%s "
                "ORDER BY id", (eid,))
    drivers_done = 0
    for uname, sraw in cur.fetchall():
        entry = {'username': uname, 'stages': json.loads(sraw or '[]')}
        finished, closed, touched = scope_participation(entry, offset, count)
        if count and closed == count:
            status = 'GREEN'
            drivers_done += 1
        elif touched:
            status = 'YELLOW'
        else:
            status = 'RED'
        mark = ' <== USER' if (user_filter and uname == user_filter) else ''
        print(f"   [{status}] {uname}: finished={finished} "
              f"closed={closed}/{count} touched={touched}{mark}")
        seg = (entry['stages'] or [])[offset:offset + count]
        for i, s in enumerate(seg):
            if not s:
                print(f"     flat {offset + i}: (empty)")
            else:
                print(f"     flat {offset + i}: time_ms={s.get('time_ms')} "
                      f"race_status={s.get('race_status', 0)}")
    print(f"   drivers_done={drivers_done}/{members}")
