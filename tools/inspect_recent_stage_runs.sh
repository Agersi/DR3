#!/bin/bash
# Dump a user's recent stage_runs with catalog distance cross-checks (read-only).
# Each row prints the uploaded distance_m next to the track's catalog length and
# their ratio (~1.0 = one full pass; ~N = N laps; >~2 on a single-pass TT is the
# accumulating-distance bug from pre-v0.1.71 clients). Camera offsets included
# for the TT camera check.
# Usage: inspect_recent_stage_runs.sh USERNAME [DAYS]   (default 4 days)
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
USERNAME="${1:?usage: inspect_recent_stage_runs.sh USERNAME [DAYS]}"
DAYS="${2:-4}"
/opt/dr3/.venv/bin/python - "$USERNAME" "$DAYS" <<'EOF'
import sys
from datetime import timedelta

from app import create_app
from app.db import db
from app.models import StageRun

user, days = sys.argv[1].strip(), int(sys.argv[2])

app = create_app()
with app.app_context():
    from dr2server.game_data import Location, TRACKS
    from datetime import datetime

    q = (db.session.query(StageRun)
         .filter(StageRun.username == user)
         .filter(StageRun.received_at >= datetime.utcnow() - timedelta(days=days))
         .order_by(StageRun.received_at, StageRun.id))
    rows = q.all()
    print('=== stage_runs for %s, last %d day(s): %d row(s)' % (user, days, len(rows)))

    def loc_name(lid):
        if lid is None:
            return '?'
        try:
            return Location(int(lid)).display_name
        except (ValueError, KeyError):
            return '?'

    def track_info(tid):
        if tid is None:
            return '?', None
        t = TRACKS.get(int(tid))
        if not t:
            return '?', None
        return t['name'], t.get('length_km')

    for r in rows:
        tname, tlen = track_info(r.track_model_id)
        if r.track_model_id is None:
            tname, tlen = track_info(r.track_id)
        ratio = ('%.2f' % (r.distance_m / (tlen * 1000.0))) if (tlen and r.distance_m) else '-'
        flag = ''
        if tlen and r.distance_m:
            rr = r.distance_m / (tlen * 1000.0)
            if r.source_type == 'timetrial' and rr > 2.0:
                flag = '  [CHECK] TT ratio > 2.0 (accumulating distance?)'
            elif rr < 0.5:
                flag = '  [CHECK] ratio < 0.5 (short/DNF pass?)'
        print('--- id=%s %s  src=%s evt=%s stage_idx=%s'
              % (r.id, r.received_at.strftime('%m-%d %H:%M'), r.source_type,
                 r.event_id or '-', r.stage_index))
        print('    track=%s (%s) loc=%s dist_m=%s catalog=%.2fkm ratio=%s%s'
              % (tname, r.track_model_id or r.track_id, loc_name(r.location_id),
                 r.distance_m, tlen or 0, ratio, flag))
        print('    time_ms=%s finished=%s end_status=%s cam=%s'
              % (r.time_ms, r.player_finished, r.player_end_status,
                 r.camera_position or '-'))
        print('    cams: bonnet=%.0f bumper=%.0f chase_close=%.0f chase_far=%.0f dash=%.0f head=%.0f'
              % (r.cam_bonnet_m or 0, r.cam_bumper_m or 0, r.cam_chase_close_m or 0,
                 r.cam_chase_far_m or 0, r.cam_dash_m or 0, r.cam_head_m or 0))
EOF
