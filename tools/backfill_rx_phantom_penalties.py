#!/usr/bin/env python3
"""Repair legacy Rallycross entries in club_results with phantom penalties.

Pre-v0.1.83 clients measured a multi-lap RX stage's penalty against the LAST
lap's clock only, so a clean 12-lap finish stored time_ms = last lap and
penalties_ms = total - last lap (a "phantom" penalty of several minutes). The
board then showed a one-lap Time next to a huge Penalty column even though the
total was right.

This tool finds every club_results stage entry whose flat slot is an RX track
and whose stored shape matches that bug (penalties_ms >= 60s AND
time_ms < penalties_ms — a real penalty never exceeds its driving time) and
folds the phantom back into the time: time_ms := total, penalties_ms := 0.
The official total is unchanged; per-lap times are unrecoverable for these
legacy runs (never stored).

Dry run by default; pass --apply to commit. Positional args restrict the scan
to specific event ids (default: every event).

Run on the VPS:
    /opt/dr3/.venv/bin/python backfill_rx_phantom_penalties.py [--apply] [EVENT_ID ...]
"""
import json
import os
import re
import sys

# Smallest possible phantom penalty is one missed lap (~40-80s); the largest
# plausible real penalty on an RX stage (8s resets / jump starts) stays far
# below this, and a real penalty can never exceed the driving time itself.
PHANTOM_MIN_MS = 60_000

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
apply = "--apply" in args
event_args = [a for a in args if not a.startswith("--")]


def flat_tracks(event_id):
    """Map flat stage index -> track id for one event (legs first, the
    stored stages list is leg-0 only on multi-leg events)."""
    cur.execute("SELECT legs_json, stages_json FROM events WHERE id=%s",
                (event_id,))
    row = cur.fetchone()
    if row is None:
        return {}
    legs_json, stages_json = row
    try:
        legs = json.loads(legs_json) if legs_json else []
    except (ValueError, TypeError):
        legs = []
    if not legs:
        try:
            legs = [{"stages": json.loads(stages_json)}] if stages_json else []
        except (ValueError, TypeError):
            return {}
    flat = {}
    i = 0
    for leg in legs:
        for st in leg.get("stages") or []:
            tr = st.get("track_id")
            if tr is not None:
                flat[i] = int(tr)
            i += 1
    return flat


if event_args:
    cur.execute("SELECT id FROM events WHERE id IN (%s)" %
                ",".join(["%s"] * len(event_args)), event_args)
    events = [r[0] for r in cur.fetchall()]
    missing = set(event_args) - set(events)
    if missing:
        sys.exit(f"unknown event ids: {sorted(missing)}")
else:
    cur.execute("SELECT id FROM events ORDER BY id")
    events = [r[0] for r in cur.fetchall()]

checked = repaired = 0
for eid in events:
    tracks = flat_tracks(eid)
    rx_slots = {i: t for i, t in tracks.items() if t in RX_TRACKS}
    if not rx_slots:
        continue
    cur.execute("SELECT id, username, stages_json FROM club_results "
                "WHERE event_id=%s", (eid,))
    for row_id, username, sj in cur.fetchall():
        try:
            seg = json.loads(sj)
        except (ValueError, TypeError):
            continue
        if not isinstance(seg, list):
            continue
        changed = False
        for i, se in enumerate(seg):
            if i not in rx_slots or not isinstance(se, dict):
                continue
            t = int(se.get("time_ms") or 0)
            pen = int(se.get("penalties_ms") or 0)
            if t <= 0 or pen < PHANTOM_MIN_MS or t >= pen:
                continue
            total = t + pen
            print(f"  {eid} {username} flat{i} track{rx_slots[i]}: "
                  f"time={t}ms pen={pen}ms -> time={total}ms pen=0")
            se["time_ms"] = total
            se["penalties_ms"] = 0
            changed = True
        if changed:
            checked += 1
            if apply:
                cur.execute("UPDATE club_results SET stages_json=%s "
                            "WHERE id=%s", (json.dumps(seg), row_id))

if not apply:
    print(f"DRY RUN — {checked} entries would be repaired "
          f"(re-run with --apply to commit)")
else:
    conn.commit()
    print(f"repaired {checked} entries")
