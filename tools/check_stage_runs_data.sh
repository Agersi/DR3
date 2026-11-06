#!/bin/bash
# Dump stage_runs rows with catalog cross-checks (read-only).
# Usage: check_stage_runs_data.sh [USERNAME]   (no arg = all users)
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
USERNAME="${1:-}"
/opt/dr3/.venv/bin/python - "$USERNAME" <<'EOF'
import sys

from app import create_app
from app.db import db
from app.models import StageRun, StageStat

user_filter = (sys.argv[1] or '').strip() or None

app = create_app()
with app.app_context():
    from dr2server.game_data import Location, TRACKS
    from app.scoring import VEHICLES, vehicle_meta

    q = db.session.query(StageRun).order_by(StageRun.received_at, StageRun.id)
    if user_filter:
        q = q.filter_by(username=user_filter)
    rows = q.all()
    print('=== stage_runs: %d row(s)%s' % (len(rows),
          ' for %s' % user_filter if user_filter else ''))

    def loc_name(lid):
        if lid is None:
            return '?'
        try:
            return Location(int(lid)).display_name
        except (ValueError, KeyError):
            return '?'

    def track_info(tid):
        # track_model_id is the catalog space (TRACKS keys, same as stage_stats);
        # track_id is the game-internal level id and does not resolve.
        if tid is None:
            return '?', None
        t = TRACKS.get(int(tid))
        if not t:
            return '?', None
        return t['name'], t.get('length_km')

    # Duplicate detection: the same stage slot recorded twice within 2 minutes
    # (the game retransmits StageComplete; the web side now collapses those).
    from datetime import timedelta
    seen = {}
    dups = []
    for r in rows:
        if r.challenge_id is None:
            continue
        key = (r.username, r.challenge_id, r.event_index, r.stage_index)
        prev = seen.get(key)
        if prev is not None and (r.received_at - prev.received_at) <= timedelta(seconds=120):
            dups.append((prev.id, r.id))
        seen[key] = r
    dup_ids = set()
    dups_for = {}
    for a, b in dups:
        dup_ids.update((a, b))
        dups_for.setdefault(a, []).append(b)
        dups_for.setdefault(b, []).append(a)

    for r in rows:
        car, cls_label = vehicle_meta(r.vehicle_id)
        cat_class = VEHICLES.get(int(r.vehicle_id), {}).get('class') \
            if r.vehicle_id is not None else None
        cls_ok = '' if r.vehicle_class_id is None or cat_class is None \
            else ('' if int(cat_class) == int(r.vehicle_class_id) else '  [CLASS MISMATCH]')
        tname, tlen = track_info(r.track_model_id)
        if r.track_model_id is None:
            tname, tlen = track_info(r.track_id)
        dist_note = ''
        if tlen and r.distance_m:
            ratio = r.distance_m / (tlen * 1000.0)
            dist_note = '  (track %.1f km, ratio %.2f)' % (tlen, ratio)
        print('--- id=%s user=%s received=%s' % (r.id, r.username, r.received_at))
        print('    source=%s event_id=%s challenge=%s evt_idx=%s stage_idx=%s'
              % (r.source_type, r.event_id or '-', r.challenge_id,
                 r.event_index, r.stage_index))
        print('    track=%s model=%s (%s) location=%s car=%s (%s)%s'
              % (r.track_id, r.track_model_id or '-', tname, loc_name(r.location_id),
                 car, cls_label, cls_ok))
        print('    time_ms=%s finished=%s end_status=%s position=%s distance_m=%s%s'
              % (r.time_ms, r.player_finished, r.player_end_status,
                 r.race_position, r.distance_m, dist_note))
        splits = r.splits_json
        if splits:
            try:
                import json as _json
                sv = _json.loads(splits)
                splits = ('[%s]' % ', '.join('%.3f' % s for s in sv[:12])) \
                    + ('...' if len(sv) > 12 else '')
            except Exception:
                pass
        print('    flat_idx=%s input_method=%s camera=%s splits=%s'
              % (r.flat_stage_index, r.input_method or '-', r.camera_position or '-',
                 splits or '-'))
        print('    wheel=%s assists=%s hardcore=%s assists_enabled=%s | input: %s / %s / %s'
              % (r.using_wheel, r.using_assists, r.hardcore_damage,
                 r.assists_enabled, r.input_library or '-',
                 (r.input_device or '-')[:40], r.input_name or '-'))
        print('    tyre=%s wear=%.2f surface_deg=%s/%.2f weather=%s vr=%s'
              % (r.tyre_compound, r.tyre_wear or 0, r.surface_degradation_enabled,
                 r.surface_degradation_level or 0, r.weather_preset_id, r.vr_enabled))
        print('    cams: bonnet=%.0f bumper=%.0f chase_close=%.0f chase_far=%.0f dash=%.0f head=%.0f'
              % (r.cam_bonnet_m or 0, r.cam_bumper_m or 0, r.cam_chase_close_m or 0,
                 r.cam_chase_far_m or 0, r.cam_dash_m or 0, r.cam_head_m or 0))
        print('    g_days=%s g_seconds=%s g_sessions=%s'
              % (r.g_days_played, r.g_seconds_played, r.g_session_count))

        # Cross-checks.
        if r.source_type == 'club' and not r.event_id:
            print('    [CHECK] club run without event_id')
        if r.source_type in ('timetrial', 'official') and r.event_id:
            print('    [CHECK] %s run with event_id=%s' % (r.source_type, r.event_id))
        if r.source_type == 'unknown' and r.event_id:
            print('    [CHECK] unknown source with event_id=%s (club marker)'
                  % r.event_id)
        if r.player_finished and not r.time_ms:
            print('    [CHECK] finished run with time_ms=0')
        if r.player_end_status != 0 and r.distance_m:
            print('    [CHECK] DNF run carries untrusted distance_m=%s' % r.distance_m)
        if r.id in dup_ids:
            print('    [CHECK] duplicate stage slot within 120s (ids %s)'
                  % ', '.join(str(i) for i in dups_for.get(r.id, [])))
        best = db.session.query(StageStat).filter_by(
            username=r.username, vehicle_id=r.vehicle_id,
            track_id=r.track_model_id).first() \
            if r.vehicle_id is not None and r.track_model_id is not None else None
        if best is not None:
            print('    stage_stats same key: best=%s ms source=%s evt=%s'
                  % (best.best_time_ms, best.source_type, best.source_event_id or '-'))
            if r.time_ms and best.best_time_ms > r.time_ms:
                print('    [CHECK] run time %d < stored best %d' % (r.time_ms, best.best_time_ms))
EOF
