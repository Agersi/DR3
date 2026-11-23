#!/usr/bin/env python3
"""Backfill best_times.run_id for rows that predate the column (leaderboard
run-metadata join target).

Matches each NULL-run_id best row to its producing stage_runs row by
(username, track_model_id, vehicle_id, conditions_id) with |time diff| <= 1000 ms
(the same tolerance _upsert_best uses across channels), restricted to clean
finishes. A compound on both sides must agree when known. Only an UNAMBIGUOUS
single candidate is assigned — a tie (two runs within a second, e.g. the best
plus a near-identical DNF close-out) is left NULL rather than guessed at.

Idempotent: rows that already carry a run_id are never touched.

Run on the VPS:  /opt/dr3/.venv/bin/python /tmp/backfill_best_times_run_id.py [--dry-run]
"""
import os
import sys
from urllib.parse import unquote, urlparse

sys.path.insert(0, "/opt/dr3")

import pymysql  # noqa: E402

TOL_MS = 1000

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

dry_run = "--dry-run" in sys.argv[1:]

cur.execute(
    "SELECT id, username, track, conditions, vehicle_id, stage_time_ms, tyre_compound "
    "FROM best_times WHERE run_id IS NULL")
orphans = cur.fetchall()
print(f"{len(orphans)} best_times row(s) without a run_id")

filled, ambiguous = 0, 0
for bid, user, track, conditions, vid, time_ms, compound in orphans:
    try:
        cond = int(conditions)
    except (TypeError, ValueError):
        continue
    cur.execute(
        "SELECT id, time_ms FROM stage_runs "
        "WHERE username=%s AND track_model_id=%s AND vehicle_id=%s "
        "AND conditions_id=%s AND player_finished=1 AND ABS(time_ms - %s) <= %s",
        (user, int(track), vid, cond, time_ms, TOL_MS))
    cands = cur.fetchall()
    if compound > 0 and cands:
        cur.execute(
            "SELECT id FROM stage_runs WHERE id IN ({}) AND tyre_compound=%s".format(
                ",".join(str(c[0]) for c in cands)), (compound,))
        ok = {r[0] for r in cur.fetchall()}
        cands = [c for c in cands if c[0] in ok]
    if len(cands) == 1:
        filled += 1
        print(f"best {bid} ({user}, track {track}): run_id -> {cands[0][0]}")
        if not dry_run:
            cur.execute("UPDATE best_times SET run_id=%s WHERE id=%s",
                        (cands[0][0], bid))
    elif len(cands) > 1:
        ambiguous += 1

if not dry_run:
    conn.commit()
print(f"done{' (dry run, nothing written)' if dry_run else ''}: "
      f"{filled} assigned, {ambiguous} ambiguous left NULL")
conn.close()
