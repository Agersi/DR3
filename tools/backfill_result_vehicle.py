#!/usr/bin/env python3
"""Backfill Result rows that predate the DNF-marker fix (backlog #32).

Two jobs, both idempotent:
  1. Every row whose vehicle_id column is empty but whose stage entries carry
     a vehicle gets the column filled (leaderboards read the column, not the
     stage entries) — this is what made terminal-damage entries show a blank
     vehicle/class in-game and on the site.
  2. With --mark-dnf EVENT_ID [...], stage entries with a real time on the
     given events are flagged race_status=4 (terminal-damage runs whose
     close-out predated the marker, so they would otherwise score rally
     points as if they were clean finishes).
  3. With --result ID [...], the same DNF flagging is scoped to specific
     result rows — for mid-leg abandons where only one driver's row on an
     event has a wrecked stage (the pre-persisted time-limit entry that the
     close-out marker landed past).

Run on the VPS:  /opt/dr3/.venv/bin/python /tmp/backfill_result_vehicle.py [--mark-dnf EVENT_ID ...] [--result ID ...]
"""
import json
import os
import sys
from urllib.parse import unquote, urlparse

sys.path.insert(0, "/opt/dr3")

import pymysql  # noqa: E402
from dr2server.game_data import VEHICLES  # noqa: E402


def car_label_for(vid):
    meta = VEHICLES.get(int(vid))
    if meta:
        return str(meta["name"])
    return f"Vehicle {int(vid)}"


args = sys.argv[1:]


def values_after(flag):
    if flag not in args:
        return []
    rest = args[args.index(flag) + 1:]
    return [a for a in rest if not a.startswith("--")]

mark_dnf_events = values_after("--mark-dnf")
mark_dnf_results = [int(a) for a in values_after("--result")]

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

p = urlparse(os.environ["DATABASE_URL"])
conn = pymysql.connect(
    host=p.hostname or "127.0.0.1", port=p.port or 3306,
    user=unquote(p.username or ""), password=unquote(p.password or ""),
    database=p.path.lstrip("/") or None,
)
cur = conn.cursor()
cur.execute("SELECT id, event_id, username, vehicle_id, stages_json FROM results")

filled, flagged = 0, 0
for rid, eid, user, vid, sj in cur.fetchall():
    stages = json.loads(sj or "[]")

    if not vid:
        entry_vid = next(
            (int(s.get("vehicle_id")) for s in stages
             if s and s.get("vehicle_id")),
            None,
        )
        if entry_vid:
            cur.execute(
                "UPDATE results SET vehicle_id=%s, car=%s WHERE id=%s",
                (entry_vid, car_label_for(entry_vid), rid),
            )
            filled += 1
            print(f"result {rid} ({eid} / {user}): vehicle_id -> {entry_vid}")

    if eid in mark_dnf_events or rid in mark_dnf_results:
        row_flags = 0
        for s in stages:
            if s and int(s.get("time_ms", 0) or 0) > 0 \
                    and int(s.get("race_status", 0) or 0) == 0:
                s["race_status"] = 4
                row_flags += 1
        if row_flags:
            cur.execute(
                "UPDATE results SET stages_json=%s WHERE id=%s",
                (json.dumps(stages), rid),
            )
            flagged += row_flags
            print(f"result {rid} ({eid} / {user}): flagged {row_flags} DNF stage(s)")

conn.commit()
print(f"done: {filled} vehicle fill(s), {flagged} DNF stage flag(s)")
conn.close()
