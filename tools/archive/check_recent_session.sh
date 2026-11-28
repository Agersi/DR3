#!/bin/bash
# Dump the recent-session state from the dr3 DB (read-only):
#   stage_runs rows in the window, best_times for the user (ghost flag),
#   club_results for every event referenced by those runs.
# Usage: check_recent_session.sh [USERNAME] [HOURS]   (defaults: all users, 4h)
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
USERNAME="${1:-}"
HOURS="${2:-4}"
/opt/dr3/.venv/bin/python - "$USERNAME" "$HOURS" <<'EOF'
import json, sys

from app import create_app
from app.db import db
from app.models import StageRun, TimeTrialEntry, Result, Event

user_filter = (sys.argv[1] or '').strip() or None
hours = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0

app = create_app()
with app.app_context():
    from datetime import timedelta
    from sqlalchemy import func

    window = db.session.query(func.now()).scalar() - timedelta(hours=hours)
    print('window: received_at >= %s (server NOW=%s)' % (window,
          db.session.query(func.now()).scalar()))

    q = db.session.query(StageRun).filter(StageRun.received_at >= window)
    if user_filter:
        q = q.filter_by(username=user_filter)
    runs = q.order_by(StageRun.received_at, StageRun.id).all()
    print('=== stage_runs in window: %d row(s)%s'
          % (len(runs), ' for %s' % user_filter if user_filter else ''))

    groups = {}
    for r in runs:
        key = (r.source_type, r.event_id or '-', r.challenge_id)
        g = groups.setdefault(key, {'n': 0, 'fin': 0, 'dnf': 0})
        g['n'] += 1
        if r.player_finished:
            g['fin'] += 1
        else:
            g['dnf'] += 1
    print('--- grouped (source, event_id, challenge_id) -> n / finished / dnf')
    for k, g in sorted(groups.items(), key=lambda kv: str(kv[0])):
        print('    %-10s evt=%-14s ch=%s : %3d runs  fin=%d dnf=%d'
              % (k[0], k[1], k[2], g['n'], g['fin'], g['dnf']))

    for r in runs:
        splits = ''
        if r.splits_json:
            try:
                sv = json.loads(r.splits_json)
                splits = 'splits[%d]' % len(sv)
            except Exception:
                splits = 'splits[bad]'
        print('--- id=%s %s recv=%s src=%s evt=%s ch=%s idx=%s/%s flat=%s'
              % (r.id, r.username, r.received_at, r.source_type,
                 r.event_id or '-', r.challenge_id, r.event_index,
                 r.stage_index, r.flat_stage_index))
        print('    track=%s model=%s loc=%s car=%s time_ms=%s fin=%s end=%s '
              'pos=%s dist=%s %s'
              % (r.track_id, r.track_model_id, r.location_id, r.vehicle_id,
                 r.time_ms, r.player_finished, r.player_end_status,
                 r.race_position, r.distance_m, splits))

    bq = db.session.query(TimeTrialEntry)
    if user_filter:
        bq = bq.filter_by(username=user_filter)
    bests = bq.order_by(TimeTrialEntry.submitted_at.desc(), TimeTrialEntry.id).all()
    print('=== best_times: %d row(s)%s (ghost=NULL flagged)'
          % (len(bests), ' for %s' % user_filter if user_filter else ''))
    for b in bests:
        ghost = ('NULL' if b.ghost_data_b64 is None
                 else '%dB' % len(b.ghost_data_b64))
        recent = ' [IN WINDOW]' if (b.submitted_at and b.submitted_at >= window) else ''
        print('    id=%s car=%s track=%s cond=%s cat=%s time=%sms ghost=%s '
              'sub=%s%s'
              % (b.id, b.vehicle_id, b.track, b.conditions, b.category,
                 b.stage_time_ms, ghost, b.submitted_at, recent))

    ev_ids = {r.event_id for r in runs if r.event_id}
    print('=== club_results for events touched in window: %s'
          % (sorted(ev_ids) or 'none'))
    for eid in sorted(ev_ids):
        evt = db.session.query(Event).filter_by(id=eid).first()
        print('--- event %s (%s)' % (eid, evt.name if evt else '?'))
        for res in db.session.query(Result).filter_by(event_id=eid).all():
            stages = json.loads(res.stages_json or '[]')
            parts = []
            for i, s in enumerate(stages):
                if not s:
                    continue
                t = s.get('time_ms', 0)
                st = s.get('race_status', s.get('status'))
                parts.append('%d:%s%s' % (i + 1, int(t) // 1000 if t else 'DNF',
                                          '' if t and st == 0 else '/st%s' % st))
            print('    user=%s total=%sms car=%s stages=[%s] attempts=%s'
                  % (res.username, res.total_time_ms, res.vehicle_id,
                     ', '.join(parts), res.attempts_used))
EOF
