#!/usr/bin/env python3
"""Backfill a DNF skip stage on an existing result row (backlog #32 follow-up).

Client v0.1.34's terminal-damage skip plan used the wrong service-area
semantics ("service after stage i" instead of "service before stage i"), so
rows created by it may be missing the DNF flag + limit time on stages that
were actually skipped (e.g. a wreck on the first stage of a leg with no
further service left in the leg — v0.1.34 let the player resume mid-leg).
This tool sets one stage entry to its limit time + race_status, mirroring
exactly what the fixed dispatcher's marker would have written:

  - time_ms is filled only when the entry has no stored time (a real time is
    never overwritten),
  - race_status is set on the entry,
  - total_time_ms is recomputed with the web's own formula.

Usage (on the VPS):
  /opt/dr3/.venv/bin/python backfill_dnf_skip.py RESULT_ID STAGE_POS TIME_MS RACE_STATUS
"""
import json
import os
import sys
from urllib.parse import unquote, urlparse

rid, pos, time_ms, race_status = (int(a) for a in sys.argv[1:5])

env = {}
with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

p = urlparse(env["DATABASE_URL"])
import pymysql  # noqa: E402

conn = pymysql.connect(
    host=p.hostname or "127.0.0.1", port=p.port or 3306,
    user=unquote(p.username or ""), password=unquote(p.password or ""),
    database=p.path.lstrip("/") or None,
)
cur = conn.cursor()
cur.execute("SELECT id, event_id, username, stages_json FROM results WHERE id=%s", (rid,))
row = cur.fetchone()
if not row:
    sys.exit(f"result {rid} not found")
_, eid, user, sj = row
stages = json.loads(sj or "[]")
while len(stages) <= pos:
    stages.append({"time_ms": 0, "penalties_ms": 0, "submitted_at": None})
entry = dict(stages[pos] or {})
changed = False
if int(entry.get("time_ms", 0) or 0) == 0 and time_ms > 0:
    entry["time_ms"] = time_ms
    changed = True
if int(entry.get("race_status", 0) or 0) != race_status:
    entry["race_status"] = race_status
    changed = True
entry.setdefault("penalties_ms", 0)
stages[pos] = entry
if changed:
    total = sum(
        s["time_ms"] + s.get("penalties_ms", 0)
        for s in stages if s.get("time_ms", 0) > 0
    )
    cur.execute(
        "UPDATE results SET stages_json=%s, total_time_ms=%s WHERE id=%s",
        (json.dumps(stages), total, rid),
    )
    conn.commit()
    print(f"result {rid} ({eid} / {user}): stage {pos} -> "
          f"time_ms={entry['time_ms']} race_status={race_status}; "
          f"total_time_ms={total}")
else:
    print(f"result {rid} ({eid} / {user}): stage {pos} already in target state "
          f"(time_ms={entry.get('time_ms')} race_status={entry.get('race_status')}); "
          f"no change")
conn.close()
