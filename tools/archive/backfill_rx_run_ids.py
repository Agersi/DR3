#!/usr/bin/env python3
"""Retroactively stamp run_id onto Rallycross best_times rows.

RX best rows are single-lap PostTime records — no stage_runs row ever exists
for an individual lap, so the live path could never point them at the run that
drove it (and pre-run_id legacy rows were missed by the original backfill).
This one-shot tool matches every still-unattributed RX best row to a stored
run whose per-lap splits contain the row's time within tolerance (lap-scale
rows), or whose total equals the row's time within tolerance (total-scale
legacy rows, where best == event total). Conditions use the relaxed rule: an
exact match, or one side NULL and the other '0' (legacy rows predate
conditions defaulting to '0').

Rows that already carry a run_id are left alone — their payload may belong to
an older run whose lap merely lands within tolerance of this one. When
several runs match, the smallest time difference wins; on an exact tie the
most recent run is taken.

Run on the VPS:
    /opt/dr3/.venv/bin/python backfill_rx_run_ids.py [--dry-run] [TRACK_ID ...]
"""
import json
import os
import re
import sys

TOLERANCE_MS = 250

# The 13 rallycross tracks (Location.discipline == 'rallycross').
RX_TRACKS = frozenset({436, 476, 478, 536, 537, 538, 565,
                       638, 639, 640, 641, 642, 643})

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
dry_run = "--dry-run" in args
track_args = [a for a in args if not a.startswith("--")]
if track_args:
    tracks = sorted(RX_TRACKS & frozenset(int(t) for t in track_args))
    if not tracks:
        sys.exit(f"none of {track_args} are RX tracks")
else:
    tracks = sorted(RX_TRACKS)

placeholders = ",".join(["%s"] * len(tracks))

cur.execute(
    f"""SELECT id, username, vehicle_id, track_model_id, conditions_id,
               splits_json, time_ms, received_at
         FROM stage_runs
         WHERE track_model_id IN ({placeholders})""", tracks)
runs_by_key: dict = {}
for run_id, username, vehicle_id, track, cond, splits, total_ms, at in cur.fetchall():
    laps = None
    if splits:
        try:
            loaded = json.loads(splits)
        except (ValueError, TypeError):
            loaded = None
        if isinstance(loaded, list) and loaded:
            laps = loaded
    runs_by_key.setdefault((username, vehicle_id, track), []).append(
        (at, cond, laps, total_ms, run_id))

cur.execute(
    f"""SELECT id, username, vehicle_id, track, conditions, stage_time_ms
        FROM best_times
        WHERE track IN ({placeholders}) AND run_id IS NULL""", tracks)
rows = cur.fetchall()
print(f"unattributed RX best rows: {len(rows)}")

updated = 0
for row_id, username, vehicle_id, track, cond, ms in rows:
    matches = []
    for at, run_cond, laps, total_ms, run_id in runs_by_key.get(
            (username, vehicle_id, int(track)), []):
        cond_ok = (str(run_cond) == cond) or (run_cond is None and cond == '0')
        if not cond_ok:
            continue
        diffs = []
        if laps:
            diffs.append((min(abs(ms - lap * 1000) for lap in laps), 'lap'))
        if total_ms is not None:
            diffs.append((abs(ms - total_ms), 'total'))
        if diffs:
            diff, kind = min(diffs)
            if diff <= TOLERANCE_MS:
                matches.append((diff, at, run_id, kind))
    if not matches:
        print(f"  MISS bt={row_id} {username} track={track} cond={cond} "
              f"{ms}ms")
        continue
    matches.sort(key=lambda m: m[1], reverse=True)   # most recent first
    matches.sort(key=lambda m: m[0])                 # then min diff (stable)
    diff, at, run_id, kind = matches[0]
    if dry_run:
        print(f"  WOULD bt={row_id} {username} track={track} cond={cond} "
              f"{ms}ms -> run={run_id} ({kind} diff {diff}ms, {at})")
    else:
        cur.execute("UPDATE best_times SET run_id=%s WHERE id=%s",
                    (run_id, row_id))
        print(f"  STAMP bt={row_id} {username} track={track} cond={cond} "
              f"{ms}ms -> run={run_id} ({kind} diff {diff}ms, {at})")
    updated += 1

if not dry_run:
    conn.commit()
print(f"{'would stamp' if dry_run else 'stamped'}: {updated}/{len(rows)}")
