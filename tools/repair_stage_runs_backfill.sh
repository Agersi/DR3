#!/bin/bash
# One-time backfill of stage_runs from results.stages_json (#56). Pre-v0.1.55
# runs never fed the per-stage detail endpoint, so lifetime stats (which now
# read stage_runs only) would lose that history. For every club Result slot
# that has no stage_runs row yet for the same (event_id, username,
# flat_stage_index), insert one:
#   - clean finish (race_status==0, real time): full row incl. distance +
#     splits when the entry carries them
#   - DNF-flagged slot (race_status!=0): player_finished=False, no time
#     (the stored limit value is not a real run), NULL distance
# Never touches existing stage_runs rows; re-running is a no-op once applied.
# Read/write: only stage_runs (reads results + events).
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
import json
from datetime import datetime

from app import create_app
from app.db import db
from app.models import Event, Result, StageRun, utcnow
from app.tables import vehicle_class_id_for_label
from dr2server.game_data import Location

LOCATION_IDS = {loc.display_name: int(loc) for loc in Location}


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


app = create_app()
with app.app_context():
    created = 0
    for r in db.session.query(Result).all():
        ev = db.session.get(Event, r.event_id)
        if ev is None:
            continue
        legs = json.loads(ev.legs_json or '[]')
        # flat position -> (event_index, stage_index, track_id, location, class)
        layout = {}
        offset = 0
        for ei, leg in enumerate(legs):
            loc_id = LOCATION_IDS.get(leg.get('location') or '')
            cls_id = vehicle_class_id_for_label(leg.get('car_class') or '')
            for si, s in enumerate(leg.get('stages') or []):
                try:
                    tid = int(s.get('track_id'))
                except (TypeError, ValueError):
                    tid = None
                layout[offset] = (ei, si, tid, loc_id, cls_id)
                offset += 1

        # Slots already recorded by a live client. v0.1.57+ rows carry the flat
        # ordinal directly; pre-v0.1.57 rows (flat NULL) stored the raw game
        # EventIndex, which is always 0 on per-leg club challenges — every such
        # row in production is a leg-0 run, so stage_idx IS the flat ordinal.
        have = set()
        stamps = []
        for row in db.session.query(
                StageRun).filter_by(event_id=r.event_id,
                                    username=r.username).all():
            if row.flat_stage_index is not None:
                have.add(row.flat_stage_index)
            elif (row.challenge_id is not None
                  and row.event_index == 0):
                have.add(row.stage_index)
            stamps.append(row.received_at)

        def already_recorded(pos, entry):
            if pos in have:
                return True
            # Belt + braces: a live row received within the retransmit window
            # of this slot's submission is the same run.
            ts = parse_ts(entry.get('submitted_at'))
            return (ts is not None and any(
                s is not None and abs((s - ts).total_seconds()) < 120
                for s in stamps))

        for pos, entry in enumerate(json.loads(r.stages_json or '[]')):
            if not isinstance(entry, dict) or already_recorded(pos, entry):
                continue
            time_ms = int(entry.get('time_ms') or 0)
            race_status = int(entry.get('race_status') or 0)
            driven = bool(entry.get('submitted_at')) or time_ms > 0
            if not driven:
                continue
            clean = race_status == 0 and time_ms > 0
            ei, si, tid, loc_id, cls_id = layout.get(
                pos, (None, None, None, None, None))
            db.session.add(StageRun(
                username=r.username,
                received_at=parse_ts(entry.get('submitted_at')) or utcnow(),
                source_type='club',
                event_id=r.event_id,
                challenge_id=None,
                event_index=ei if ei is not None else 0,
                stage_index=si if si is not None else pos,
                track_id=None,
                track_model_id=tid,
                location_id=loc_id,
                vehicle_id=entry.get('vehicle_id') or r.vehicle_id,
                vehicle_class_id=cls_id,
                time_ms=time_ms if clean else 0,
                player_finished=clean,
                player_end_status=race_status,
                race_position=0,
                distance_m=(int(entry['distance_driven'])
                            if clean and entry.get('distance_driven') else None),
                using_wheel=bool(entry.get('using_wheel')),
                using_assists=bool(entry.get('using_assists')),
                splits_json=(json.dumps(entry['splits'])
                             if isinstance(entry.get('splits'), list)
                             and entry['splits'] else None),
                flat_stage_index=pos,
            ))
            created += 1
    db.session.commit()
    print('repair_stage_runs_backfill: created=%d' % created)
EOF
