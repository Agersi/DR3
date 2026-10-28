#!/usr/bin/env python3
"""Inspect stored DNF/terminal-damage result rows (read-only).

Shows Result rows whose stage entries carry a time-limit value (the stage's
time limit is what a wrecked/DNF close-out reports as StageTime), so the
vehicle/class provenance of DNF entries can be checked against what the
leaderboard and site render.

Run on the VPS:  /opt/dr3/.venv/bin/python /tmp/inspect_dnf_results.py [event_id] [username]
(both filters optional; default = every row with a 900000/1800000 ms value)
"""
import json
import os
import sys
from urllib.parse import unquote, urlparse

ENV_PATH = "/opt/dr3/web/.env"
with open(ENV_PATH) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import pymysql  # noqa: E402

p = urlparse(os.environ["DATABASE_URL"])
conn = pymysql.connect(
    host=p.hostname or "127.0.0.1", port=p.port or 3306,
    user=unquote(p.username or ""), password=unquote(p.password or ""),
    database=p.path.lstrip("/") or None,
)

event_id = sys.argv[1] if len(sys.argv) > 1 else None
username = sys.argv[2] if len(sys.argv) > 2 else None

cur = conn.cursor()
sql = (
    "SELECT r.id, e.name, c.name, r.event_id, r.username, r.vehicle_id, r.car,"
    "       r.total_time_ms, r.attempts_used, r.in_progress_json, r.stages_json,"
    "       e.start_time, e.end_time"
    " FROM results r"
    " LEFT JOIN events e ON e.id = r.event_id"
    " LEFT JOIN clubs c ON c.id = e.club_id"
)
where, args = [], []
if event_id:
    where.append("r.event_id = %s")
    args.append(event_id)
if username:
    where.append("r.username = %s")
    args.append(username)
if not (event_id or username):
    where.append("(r.total_time_ms IN (900000, 1800000)"
                 " OR r.stages_json LIKE %s)")
    args.append("%900000%")
sql += " WHERE " + " AND ".join(where) + " ORDER BY r.id DESC LIMIT 10"

cur.execute(sql, tuple(args))
for row in cur.fetchall():
    rid, evname, club, eid, user, vid, car, total, att, ipj, sj, st, et = row
    print(f"--- result id={rid} club={club!r} event={evname!r} ({eid})")
    print(f"    user={user!r} vehicle_id={vid} car={car!r} total_ms={total} "
          f"attempts_used={att}")
    print(f"    window: {st} -> {et}")
    print(f"    in_progress: {ipj}")
    print(f"    stages: {sj}")
conn.close()
