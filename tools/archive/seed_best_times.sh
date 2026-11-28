#!/bin/bash
# Seed best_times from every clean run already on record (v0.1.58): before this
# table only held time-trial PostTime submissions, but the personal best is now
# tracked across ALL run types — so backfill it from:
#   1. stage_runs: every clean-finished row (club/official/timetrial) with a
#      car + track + real time; conditions from the row when present, else '0'
#      (pre-v0.1.58 rows never captured them).
#   2. club_results.stages_json: pre-v0.1.55 club runs (clean entries with a
#      real time below their limit), tracks resolved from the event legs exactly
#      like the #56 stage_runs backfill.
# Every candidate goes through game_api._upsert_best, so existing rows are only
# touched by strictly-faster times (or equal-ms absorption of missing
# splits/ghost) — a stored TT best is replaced when history shows a faster run
# in the same car on the same track. Idempotent: re-running changes nothing
# once applied (all candidates already at or below stored bests).
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
import json

from app import create_app
from app.db import db
from app.game_api import _upsert_best
from app.models import Event, Result, StageRun, TimeTrialEntry
from app.scoring import VEHICLES  # re-exported from dr2server.game_data
from app.tables import vehicle_class_id_for_label


def class_for(vid, stored=None):
    if stored:
        return int(stored)
    meta = VEHICLES.get(int(vid or 0))
    return int(meta['class']) if meta and meta.get('class') else None


def snapshot():
    out = {}
    for row in db.session.query(TimeTrialEntry).all():
        out[(row.username, row.vehicle_id, str(row.track),
             str(row.conditions))] = (
            row.stage_time_ms or 0,
            bool(row.ghost_data_b64), bool(row.splits_json))
    return out


app = create_app()
with app.app_context():
    before = snapshot()

    # 1. stage_runs clean finishes.
    n_runs = 0
    for row in db.session.query(StageRun).filter(
            StageRun.player_finished.is_(True),
            StageRun.time_ms > 0,
            StageRun.vehicle_id.isnot(None),
            StageRun.track_model_id.isnot(None)).all():
        _upsert_best(
            row.username, row.vehicle_id, str(row.track_model_id),
            str(row.conditions_id if row.conditions_id is not None else '0'),
            int(row.time_ms),
            vclass=class_for(row.vehicle_id, row.vehicle_class_id),
            using_wheel=bool(row.using_wheel),
            using_assists=bool(row.using_assists),
            splits=json.loads(row.splits_json) if row.splits_json else None)
        n_runs += 1

    # 2. pre-v0.1.55 club runs from club_results.stages_json.
    n_club = 0
    for r in db.session.query(Result).all():
        ev = db.session.get(Event, r.event_id)
        if ev is None:
            continue
        tracks = []
        classes = []
        for leg in json.loads(ev.legs_json or '[]'):
            cls = vehicle_class_id_for_label(leg.get('car_class') or '')
            for s in leg.get('stages') or []:
                try:
                    tracks.append(int(s.get('track_id')))
                except (TypeError, ValueError):
                    tracks.append(None)
                classes.append(cls)
        for pos, entry in enumerate(json.loads(r.stages_json or '[]')):
            if not isinstance(entry, dict):
                continue
            try:
                time_ms = int(entry.get('time_ms') or 0)
                race_status = int(entry.get('race_status') or 0)
            except (TypeError, ValueError):
                continue
            clean = race_status == 0 and time_ms > 0
            if not clean or pos >= len(tracks) or tracks[pos] is None:
                continue
            vid = entry.get('vehicle_id') or r.vehicle_id
            if not vid:
                continue
            _upsert_best(
                r.username, int(vid), str(tracks[pos]), '0', time_ms,
                vclass=class_for(vid, classes[pos] if pos < len(classes) else None),
                using_wheel=bool(entry.get('using_wheel')),
                using_assists=bool(entry.get('using_assists')),
                splits=entry.get('splits')
                if isinstance(entry.get('splits'), list) else None)
            n_club += 1

    db.session.commit()
    after = snapshot()

    created = replaced = absorbed = 0
    for key, (ms, ghost, splits) in after.items():
        if key not in before:
            created += 1
            continue
        b_ms, b_ghost, b_splits = before[key]
        if ms < b_ms:
            replaced += 1
        elif (not b_ghost and ghost) or (not b_splits and splits):
            absorbed += 1
    print('seed_best_times: candidates stage_runs=%d club_entries=%d | '
          'best_rows before=%d after=%d created=%d replaced_faster=%d '
          'absorbed=%d' % (n_runs, n_club, len(before), len(after), created,
                           replaced, absorbed))
EOF
