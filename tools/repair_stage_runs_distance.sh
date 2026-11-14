#!/bin/bash
# Repair historical time-trial stage_runs rows whose distance_m (and cam_*_m)
# were stored from the game's CUMULATIVE camera counters: the game accumulates
# those counters across repeats of the same track in one session, so a repeat
# run's row carried the sum of all attempts, not the single-run distance.
# Client v0.1.70 corrects this at emit time; this tool fixes the rows stored
# before it. Read-only by default — pass --apply to write.
#
# Per (user, track_model_id), in received_at order, finished TT rows only:
#   - raw ~= catalog stage length            -> fresh counter, already correct
#   - else delta vs previous row ~= length   -> accumulation: store the delta
#     (exact; cam columns get their per-camera deltas too)
#   - else (no previous row to delta against — client restart mid-session,
#     a DNF gap, ...)                        -> store the catalog stage length
#     as the best available estimate (cam columns scaled proportionally)
# Idempotent: repaired rows pass the first branch on re-runs.
# Usage: repair_stage_runs_distance.sh [--apply] [USERNAME]
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
APPLY="${1:-}"
USERNAME="${2:-}"
/opt/dr3/.venv/bin/python - "$APPLY" "$USERNAME" <<'EOF'
import sys

from app import create_app
from app.db import db
from app.models import StageRun

apply = (sys.argv[1] or '').strip() == '--apply'
user_filter = (sys.argv[2] or '').strip() or None
TOL = 0.35  # same tolerance as the client-side correction

CAM = ('cam_bonnet_m', 'cam_bumper_m', 'cam_chase_close_m',
       'cam_chase_far_m', 'cam_dash_m', 'cam_head_m')

app = create_app()
with app.app_context():
    from dr2server.game_data import TRACKS

    q = db.session.query(StageRun).filter(
        StageRun.source_type == 'timetrial',
        StageRun.player_end_status == 0,
        StageRun.distance_m.isnot(None),
        StageRun.track_model_id.isnot(None),
    ).order_by(StageRun.received_at, StageRun.id)
    if user_filter:
        q = q.filter_by(username=user_filter)
    rows = q.all()

    fixed_delta, fixed_catalog = [], []
    by_track = {}
    for r in rows:
        by_track.setdefault((r.username, int(r.track_model_id)), []).append(r)

    for (user, tid), group in sorted(by_track.items()):
        length_m = (TRACKS.get(tid) or {}).get('length_km', 0.0) * 1000.0
        prev = None  # previous row's ORIGINAL raw values
        for r in group:
            raw = float(r.distance_m)
            raw_cam = {c: float(getattr(r, c) or 0.0) for c in CAM}
            if length_m and abs(raw - length_m) <= TOL * length_m:
                pass  # fresh counter (or already repaired) — correct as-is
            elif prev is not None and raw <= prev['dist'] \
                    and (not length_m or raw <= (1 + TOL) * length_m):
                # Counter reset between rows — a genuinely fresh value can be
                # at most ~one stage long, so an over-long value that merely
                # went backwards is still inflated (new epoch, attempts
                # already banked before this row).
                pass
            elif length_m and prev is not None \
                    and abs(raw - prev['dist'] - length_m) <= TOL * length_m:
                for c in CAM:
                    setattr(r, c, max(0.0, (getattr(r, c) or 0.0)
                                       - prev['cam'].get(c, 0.0)))
                r.distance_m = int(round(raw - prev['dist']))
                fixed_delta.append((r.id, user, tid, int(raw),
                                    int(raw - prev['dist'])))
            elif length_m:
                # Inflated with no usable previous row (client restart mid-
                # session) or a gap in the chain — catalog length is the best
                # available estimate.
                scale = length_m / raw
                for c in CAM:
                    setattr(r, c, round((getattr(r, c) or 0.0) * scale, 1))
                r.distance_m = int(round(length_m))
                fixed_catalog.append((r.id, user, tid, int(raw)))
            # prev tracks the ORIGINAL raw values so one pass repairs a whole
            # accumulation chain (and re-runs see already-repaired rows as fresh).
            prev = {'dist': raw, 'cam': raw_cam}

    if apply:
        db.session.commit()

    print('repair_stage_runs_distance: %d TT row(s) scanned, mode=%s'
          % (len(rows), 'APPLY' if apply else 'report-only'))
    for rid, user, tid, raw, new in fixed_delta:
        print('  [delta]    id=%s %s track=%s: %d -> %d m (exact)'
              % (rid, user, tid, raw, new))
    for rid, user, tid, raw in fixed_catalog:
        print('  [catalog]  id=%s %s track=%s: %d -> catalog length (estimate)'
              % (rid, user, tid, raw))
    if not fixed_delta and not fixed_catalog:
        print('  nothing to repair')
EOF
