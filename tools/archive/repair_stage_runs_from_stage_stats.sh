#!/bin/bash
# Salvage stage_stats best rows into stage_runs before the table is dropped
# (v0.1.58 consolidation). stage_stats only ever stored ONE row per
# user+car+track (the best), so this adds at most one representative row per
# combo — it never restores full run history:
#   - 'timetrial' rows are SKIPPED: best_times (renamed time_trials) is the
#     authoritative TT store and post-v0.1.55 TT runs already have real
#     stage_runs rows — inserting the bests would double-count runs.
#   - 'club' rows insert only when NO stage_runs row exists for the same
#     (username, vehicle_id, track_model_id) at all — club history was already
#     backfilled from club_results.stages_json (#56), so this is a gap-filler.
#   - 'official' rows insert only when no stage_runs row with source_type
#     'official' exists for the same combo — post-v0.1.55 dailies have real
#     rows, pre-v0.1.55 daily bests get one representative row.
# Inserted rows: player_finished=True, time = stored best, splits kept when
# present, NULL distance (stage_stats never stored it), class from the VEHICLES
# catalog, received_at = the stats row's updated_at.
# Idempotent: re-running is a no-op once applied.
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
import json

from app import create_app
from app.db import db
from app.models import StageRun, utcnow
from app.scoring import VEHICLES  # re-exported from dr2server.game_data

app = create_app()
with app.app_context():
    insp = db.inspect(db.engine)
    if 'stage_stats' not in insp.get_table_names():
        print('repair_stage_runs_from_stage_stats: stage_stats absent - nothing to do')
        raise SystemExit(0)
    cols = {c['name'] for c in insp.get_columns('stage_stats')}

    rows = db.session.execute(db.text(
        "SELECT username, vehicle_id, track_id, best_time_ms, splits_json,"
        " source_type, source_event_id, updated_at FROM stage_stats")).all()

    created = {'club': 0, 'official': 0}
    skipped_timetrial = 0
    for (user, vid, tid, ms, splits, src, evt, stamp) in rows:
        if src == 'timetrial':
            skipped_timetrial += 1
            continue
        if src not in ('club', 'official') or not vid or tid is None \
                or not ms:
            continue
        combos = db.session.query(StageRun).filter_by(
            username=user, vehicle_id=vid, track_model_id=tid).all()
        if src == 'club':
            if combos:
                continue
        elif any(r.source_type == 'official' for r in combos):
            continue
        db.session.add(StageRun(
            username=user,
            received_at=stamp or utcnow(),
            source_type=src,
            event_id=str(evt)[:64] if (src == 'club' and evt) else None,
            challenge_id=None,
            event_index=0,
            stage_index=0,
            track_id=None,
            track_model_id=int(tid),
            location_id=None,
            vehicle_id=int(vid),
            vehicle_class_id=(VEHICLES.get(int(vid) or 0) or {}).get('class'),
            time_ms=int(ms),
            player_finished=True,
            player_end_status=0,
            race_position=0,
            distance_m=None,
            splits_json=splits if isinstance(splits, str) else None,
            flat_stage_index=None,
        ))
        created[src] += 1
    db.session.commit()
    print('repair_stage_runs_from_stage_stats: created club=%d official=%d '
          'skipped_timetrial=%d' % (created['club'], created['official'],
                                    skipped_timetrial))
EOF
