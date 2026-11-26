#!/usr/bin/env python3
"""Backfill run metadata left NULL by older ingest code.

Three independent repairs, all idempotent (each only touches rows still in
the broken state):

A) stage_runs.camera_position — re-derive from the stored cam_*_m metres with
   the exact rule the current ingest uses (the channel that accumulated the
   most metres). Older code nulled the camera on DNF-flagged runs and pre-
   v0.1.57 rows never derived it at all, so 124+ rows carry real cam metres
   with a NULL camera_position (blank Camera cells on the club page and the
   leaderboard).

B) best_times.run_id for unlinked Rallycross rows — a best lap recorded before
   run linking existed points at no stage_runs row, so the leaderboard shows
   blank Source/Input/Camera/Spares/Penalty cells. Candidates are the driver's
   stage_runs for the same car + exact track (a wider location match would
   attribute another track's setup). A candidate whose conditions_id differs
   from the best row's is dropped (NULL counts as unknown and stays eligible).
   The producing run's server receive time hugs the best row's submitted_at
   (the client files the best at stage end; observed gaps are seconds to ~2
   min), so a candidate received more than --max-age away is rejected. With
   exactly one surviving candidate the run_id is stamped; with several, the
   closest one is only STAMPED when --closest is passed (otherwise it is
   reported for a human decision).

C) stage_runs.flat_stage_index for club rows — legacy clients did not send the
   flat slot, so those rows are invisible to the club board's per-slot join.
   Single-leg events: the in-leg stage index IS the flat slot. Multi-leg
   events: the slot is the unique layout position whose track AND in-leg
   position both match the row (legacy event_index values are unreliable).
   A second attempt at an already-keyed slot gets the same slot — the board
   keeps the latest row per slot either way.

D) best_times.run_id for unlinked NON-RX rows left by the pre-2026-11-26
   ingest (a snapshot whose ms sat a hair over the PostTime's fell through
   both the faster and the equal-ms branches, so run_id stayed NULL).
   Candidates are the driver's FINISHED stage_runs for the same car + exact
   track + conditions whose time is within 1 s of the best row's (the two
   channels read the same game clock as round vs truncate) and received at
   most --max-age away from the row's submitted_at. RX tracks are excluded —
   their best rows are single laps, which no run total can match (section B
   covers them). With exactly one surviving candidate the run_id is stamped;
   with several, the closest is only STAMPED when --closest is passed.

Dry run by default; pass --apply to commit.

Run on the VPS:
    /opt/dr3/.venv/bin/python backfill_run_metadata.py [--apply] [--closest]
"""
import json
import os
import re
import sys

# The 13 rallycross tracks (Location.discipline == 'rallycross').
RX_TRACKS = frozenset({436, 476, 478, 536, 537, 538, 565,
                       638, 639, 640, 641, 642, 643})

CAM_FIELDS = (('cam_bonnet_m', 'bonnet'), ('cam_bumper_m', 'bumper'),
              ('cam_chase_close_m', 'chase_close'), ('cam_chase_far_m', 'chase_far'),
              ('cam_dash_m', 'dash'), ('cam_head_m', 'head'))

args = sys.argv[1:]
apply = "--apply" in args
allow_closest = "--closest" in args
max_age_s = 1800.0
for a in args:
    if a.startswith("--max-age="):
        max_age_s = float(a.split("=", 1)[1])

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


def event_legs(event_id):
    """[[track_id per stage] per leg] for an event, legs first (the stored
    stages list is leg-0 only on multi-leg events); None when unreadable."""
    cur.execute("SELECT legs_json, stages_json FROM events WHERE id=%s",
                (event_id,))
    row = cur.fetchone()
    if row is None:
        return None
    legs_json, stages_json = row
    try:
        legs = json.loads(legs_json) if legs_json else []
    except (ValueError, TypeError):
        legs = []
    if not legs:
        try:
            legs = [{"stages": json.loads(stages_json)}] if stages_json else []
        except (ValueError, TypeError):
            return None
    out = []
    for leg in legs:
        tracks = []
        for st in leg.get("stages") or []:
            tr = st.get("track_id")
            tracks.append(int(tr) if tr is not None else None)
        out.append(tracks)
    return out or None


# ---------------------------------------------------------------- A) camera
print("=== A) stage_runs.camera_position from stored cam metres ===")
cur.execute("SELECT id, " + ", ".join(c for c, _ in CAM_FIELDS) + """
             FROM stage_runs WHERE camera_position IS NULL AND
             (%s)""" % " OR ".join(f"{c} > 0" for c, _ in CAM_FIELDS))
cam_fixed = 0
for row in cur.fetchall():
    rid = row[0]
    metres = {name: (row[i + 1] or 0.0) for i, (_, name) in enumerate(CAM_FIELDS)}
    cam = max(metres, key=metres.get) if any(metres.values()) else None
    if cam is None:
        continue
    print(f"  run{rid}: {cam} ({max(metres.values()):.0f} m)")
    cam_fixed += 1
    if apply:
        cur.execute("UPDATE stage_runs SET camera_position=%s WHERE id=%s",
                    (cam, rid))

# ------------------------------------------------- B) best_times.run_id (RX)
print("\n=== B) best_times.run_id for unlinked RX rows ===")
ph = ",".join(["%s"] * len(RX_TRACKS))
cur.execute(f"SELECT id, username, vehicle_id, track, conditions, stage_time_ms, "
            f"submitted_at FROM best_times WHERE run_id IS NULL AND "
            f"stage_time_ms > 0 AND track IN ({ph}) ORDER BY username", tuple(RX_TRACKS))
stamped = skipped = 0
for bt_id, uname, vid, track, cond, ms, submitted_at in cur.fetchall():
    if submitted_at is None:
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: no submitted_at, skip")
        skipped += 1
        continue
    cur.execute("SELECT id, conditions_id, received_at FROM stage_runs "
                "WHERE username=%s AND vehicle_id=%s AND track_model_id=%s",
                (uname, vid, int(track)))
    cands = [(rid, rc, ra) for rid, rc, ra in cur.fetchall()
             if rc is None or str(rc) == str(cond)]
    if not cands:
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: no candidate run, skip")
        skipped += 1
        continue
    cands.sort(key=lambda c: abs((c[2] - submitted_at).total_seconds()))
    best = cands[0]
    gap = abs((best[2] - submitted_at).total_seconds())
    if gap > max_age_s:
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: closest run{best[0]} "
              f"{gap / 60:.1f} min away (> {max_age_s / 60:.0f}), skip")
        skipped += 1
        continue
    if len(cands) == 1:
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: unique run{best[0]} "
              f"(gap {gap:.0f}s) -> stamp")
        stamped += 1
        if apply:
            cur.execute("UPDATE best_times SET run_id=%s WHERE id=%s",
                        (best[0], bt_id))
    elif allow_closest:
        runner_up = abs((cands[1][2] - submitted_at).total_seconds())
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: {len(cands)} candidates, "
              f"closest run{best[0]} (gap {gap:.0f}s vs next {runner_up:.0f}s) "
              f"-> stamp (--closest)")
        stamped += 1
        if apply:
            cur.execute("UPDATE best_times SET run_id=%s WHERE id=%s",
                        (best[0], bt_id))
    else:
        runner_up = abs((cands[1][2] - submitted_at).total_seconds())
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: AMBIGUOUS "
              f"{len(cands)} candidates — closest run{best[0]} (gap {gap:.0f}s) "
              f"vs run{cands[1][0]} ({runner_up:.0f}s); re-run with --closest "
              f"to stamp the closest")
        skipped += 1

# ------------------------------------------------- C) flat_stage_index (club)
print("\n=== C) stage_runs.flat_stage_index for club rows ===")
cur.execute("SELECT id, username, event_id, stage_index, track_model_id "
            "FROM stage_runs WHERE event_id IS NOT NULL AND "
            "flat_stage_index IS NULL ORDER BY event_id, username, id")
flat_fixed = 0
legs_cache: dict = {}
for rid, uname, eid, st_idx, tr in cur.fetchall():
    if eid not in legs_cache:
        legs_cache[eid] = event_legs(eid)
    legs = legs_cache[eid]
    if legs is None:
        print(f"  run{rid} {uname} {eid}: unknown layout, skip")
        continue
    s = st_idx or 0
    cands = []
    if len(legs) == 1:
        # Single-leg event: the in-leg index IS the flat slot.
        if s < len(legs[0]):
            cands = [s]
    else:
        # Multi-leg: slots whose track matches AND whose in-leg position
        # equals the row's stage_index (event_index is unreliable on legacy
        # rows; a second attempt at an already-keyed slot is fine — the
        # board keeps the latest row per slot).
        offset = 0
        for leg in legs:
            for j, leg_tr in enumerate(leg):
                if tr is not None and leg_tr == tr and j == s:
                    cands.append(offset + j)
            offset += len(leg)
    if len(cands) != 1:
        print(f"  run{rid} {uname} {eid}: stage{s} track{tr} -> "
              f"{len(cands)} matching slots, skip")
        continue
    flat = cands[0]
    print(f"  run{rid} {uname} {eid}: stage{s} track{tr} -> flat{flat}")
    flat_fixed += 1
    if apply:
        cur.execute("UPDATE stage_runs SET flat_stage_index=%s WHERE id=%s",
                    (flat, rid))

# ------------------------------------------- D) best_times.run_id (non-RX)
print("\n=== D) best_times.run_id for unlinked non-RX rows ===")
ph = ",".join(["%s"] * len(RX_TRACKS))
cur.execute(f"SELECT id, username, vehicle_id, track, conditions, stage_time_ms, "
            f"submitted_at FROM best_times WHERE run_id IS NULL AND "
            f"stage_time_ms > 0 AND track NOT IN ({ph}) ORDER BY username",
            tuple(RX_TRACKS))
d_stamped = d_skipped = 0
for bt_id, uname, vid, track, cond, ms, submitted_at in cur.fetchall():
    if submitted_at is None:
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: no submitted_at, skip")
        d_skipped += 1
        continue
    cur.execute("SELECT id, conditions_id, time_ms, player_finished, received_at "
                "FROM stage_runs WHERE username=%s AND vehicle_id=%s "
                "AND track_model_id=%s", (uname, vid, int(track)))
    cands = []
    for rid, rc, rms, fin, ra in cur.fetchall():
        if rc is not None and str(rc) != str(cond):
            continue
        if rms is None or abs(rms - ms) > 1000:
            continue
        if not fin:
            continue
        if ra is None or abs((ra - submitted_at).total_seconds()) > max_age_s:
            continue
        cands.append((rid, abs(rms - ms),
                      abs((ra - submitted_at).total_seconds())))
    if not cands:
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: no candidate run, skip")
        d_skipped += 1
        continue
    cands.sort(key=lambda c: (c[1], c[2]))
    best = cands[0]
    if len(cands) == 1:
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: unique run{best[0]} "
              f"(Δ{best[1]}ms, gap {best[2]:.0f}s) -> stamp")
        d_stamped += 1
        if apply:
            cur.execute("UPDATE best_times SET run_id=%s WHERE id=%s",
                        (best[0], bt_id))
    elif allow_closest:
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: {len(cands)} candidates, "
              f"closest run{best[0]} (Δ{best[1]}ms vs next Δ{cands[1][1]}ms) "
              f"-> stamp (--closest)")
        d_stamped += 1
        if apply:
            cur.execute("UPDATE best_times SET run_id=%s WHERE id=%s",
                        (best[0], bt_id))
    else:
        print(f"  bt{bt_id} {uname} car{vid} tr{track}: AMBIGUOUS "
              f"{len(cands)} candidates — closest run{best[0]} (Δ{best[1]}ms, "
              f"gap {best[2]:.0f}s) vs run{cands[1][0]} (Δ{cands[1][1]}ms); "
              f"re-run with --closest to stamp the closest")
        d_skipped += 1

if not apply:
    print(f"\nDRY RUN — A:{cam_fixed} camera, B:{stamped} run_id "
          f"({skipped} skipped), C:{flat_fixed} flat index, "
          f"D:{d_stamped} run_id ({d_skipped} skipped) "
          f"(re-run with --apply to commit)")
else:
    conn.commit()
    print(f"\napplied — A:{cam_fixed} camera, B:{stamped} run_id, "
          f"C:{flat_fixed} flat index, D:{d_stamped} run_id")
