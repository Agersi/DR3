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

With --clear the stage entry is RESET instead (time/penalties/submitted_at to
zero/null and the matching in_progress key dropped) — for undoing a wrongly
flagged/pre-persisted stage after a mis-resolved DNF skip plan:

  /opt/dr3/.venv/bin/python backfill_dnf_skip.py RESULT_ID STAGE_POS TIME_MS RACE_STATUS
  /opt/dr3/.venv/bin/python backfill_dnf_skip.py RESULT_ID STAGE_POS --clear
"""
import json
import os
import sys
from urllib.parse import unquote, urlparse

_args = [a for a in sys.argv[1:] if a != "--clear"]
CLEAR = len(sys.argv[1:]) > len(_args)
if CLEAR:
    rid, pos = (int(a) for a in _args[:2])
else:
    rid, pos, time_ms, race_status = (int(a) for a in _args[:4])

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
cur.execute(
    "SELECT id, event_id, username, stages_json, in_progress_json "
    "FROM results WHERE id=%s", (rid,))
row = cur.fetchone()
if not row:
    sys.exit(f"result {rid} not found")
_, eid, user, sj, ipj = row
stages = json.loads(sj or "[]")
in_progress = json.loads(ipj or "{}")
while len(stages) <= pos:
    stages.append({"time_ms": 0, "penalties_ms": 0, "submitted_at": None})

if CLEAR:
    before = dict(stages[pos] or {})
    dropped_ip = in_progress.pop(str(pos), None)
    stages[pos] = {"time_ms": 0, "penalties_ms": 0, "submitted_at": None}
    total = sum(
        s["time_ms"] + s.get("penalties_ms", 0)
        for s in stages if s.get("time_ms", 0) > 0
    )
    cur.execute(
        "UPDATE results SET stages_json=%s, in_progress_json=%s, "
        "total_time_ms=%s WHERE id=%s",
        (json.dumps(stages), json.dumps(in_progress), total, rid),
    )
    conn.commit()
    print(f"result {rid} ({eid} / {user}): stage {pos} cleared "
          f"(was time_ms={before.get('time_ms')} "
          f"race_status={before.get('race_status')}, "
          f"in_progress dropped={'yes' if dropped_ip is not None else 'no'}); "
          f"total_time_ms={total}")
    conn.close()
    sys.exit(0)

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
