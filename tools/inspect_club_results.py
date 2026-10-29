#!/usr/bin/env python3
"""Dump a club's v2 championship legs and every result row with full stage
entries (read-only). Used to verify DNF markers / limit times / race_status
after terminal-damage tests.

Run on the VPS:  /opt/dr3/.venv/bin/python /tmp/inspect_club_results.py <club_id> [username]
"""
import json
import os
import re
import sys

ENV_PATH = "/opt/dr3/web/.env"
with open(ENV_PATH) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import pymysql  # noqa: E402

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             os.environ["DATABASE_URL"])
user, pw, host, port, dbname = (p.group(1), p.group(2), p.group(3),
                                int(p.group(4) or 3306), p.group(5).split("?")[0])
conn = pymysql.connect(host=host, port=port, user=user, password=pw, database=dbname)
cur = conn.cursor()

club_id = sys.argv[1]
username = sys.argv[2] if len(sys.argv) > 2 else None

print(f"== legs of {club_id} ==")
cur.execute(
    "SELECT id, name, start_time, end_time, legs_json FROM events"
    " WHERE club_id=%s AND schema_version=2 ORDER BY start_time", (club_id,))
legs = cur.fetchall()
for eid, name, st, et, lj in legs:
    try:
        L = json.loads(lj) if isinstance(lj, str) else (lj or [])
    except Exception:
        L = []
    print(f"--- {eid} | {name} | {st} -> {et}")
    for i, leg in enumerate(L):
        loc = leg.get("location_id") or leg.get("location")
        print(f"  leg[{i}]: location_id={loc!r} class={leg.get('car_class')!r}"
              f" stages={leg.get('stage_count', leg.get('stages'))!r}"
              f" rx_laps={leg.get('rx_laps')!r}")

print()
print("== results ==")
sql = ("SELECT r.id, e.name, r.username, r.vehicle_id, r.car, r.total_time_ms,"
       " r.attempts_used, r.in_progress_json, r.stages_json"
       " FROM results r JOIN events e ON e.id=r.event_id"
       " WHERE e.club_id=%s")
args = [club_id]
if username:
    sql += " AND r.username=%s"
    args.append(username)
sql += " ORDER BY r.id"
cur.execute(sql, args)
for rid, name, user_, veh, car, tot, att, inprog, stages in cur.fetchall():
    print(f"--- result {rid} | leg={name} | user={user_} | veh={veh} ({car})"
          f" | total={tot} | attempts={att}")
    print(f"  in_progress: {inprog}")
    try:
        st = json.loads(stages) if isinstance(stages, str) else (stages or [])
    except Exception:
        st = []
    for i, s in enumerate(st):
        print(f"  stage[{i}]: {json.dumps(s)}")
