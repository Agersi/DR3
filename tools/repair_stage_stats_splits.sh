#!/bin/bash
# Idempotent backfill of stage_stats.splits_json from run data that already
# carries the splits. The pre-v0.1.56 web dropped telemetry splits on equal-
# time same-run submissions (the post-finish leaderboard pre-persist created
# the row time-only, so the StageComplete's identical time + splits was never
# stored):
#   - club rows: splits come from results.stages_json entries of the SAME run
#     (same track + exact best time) on the row's source event
#   - timetrial rows: splits come from the matching time_trials entry
#     (same car+track, same stage time)
# Only fills NULLs; never overwrites stored splits or changes times. Re-running
# is a no-op once applied. Read/write: only stage_stats.
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
import json

from app import create_app
from app.db import db
from app.models import Event, Result, StageStat, TimeTrialEntry

app = create_app()
with app.app_context():
    rows = db.session.query(StageStat).filter(
        StageStat.splits_json.is_(None)).all()
    fixed = []
    for stat in rows:
        splits = None
        if stat.source_event_id:
            r = db.session.query(Result).filter_by(
                username=stat.username,
                event_id=stat.source_event_id).first()
            ev = db.session.get(Event, stat.source_event_id)
            if r is not None and ev is not None:
                tracks = []
                for leg in json.loads(ev.legs_json or '[]'):
                    for s in leg.get('stages') or []:
                        try:
                            tracks.append(int(s.get('track_id')))
                        except (TypeError, ValueError):
                            tracks.append(None)
                for pos, entry in enumerate(json.loads(r.stages_json or '[]')):
                    if (pos < len(tracks) and tracks[pos] == stat.track_id
                            and int(entry.get('time_ms') or 0) == stat.best_time_ms
                            and isinstance(entry.get('splits'), list)
                            and entry['splits']):
                        splits = entry['splits']
                        break
        else:
            for tt in db.session.query(TimeTrialEntry).filter_by(
                    username=stat.username, vehicle_id=stat.vehicle_id,
                    track=str(stat.track_id)):
                if tt.stage_time_ms == stat.best_time_ms and tt.splits_json:
                    try:
                        splits = json.loads(tt.splits_json)
                    except ValueError:
                        splits = None
                    break
        if splits is not None:
            stat.splits_json = json.dumps(splits)
            fixed.append((stat.id, stat.username, stat.track_id))
    db.session.commit()
    print('repair_stage_stats_splits: filled=%s' % (fixed or '-'))
EOF
