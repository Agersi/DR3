#!/bin/bash
# Verifies the stage_runs table + the new users profile columns on the VPS,
# in particular that the additive migrations ran at boot:
#   - stage_runs table exists with the full column set
#   - users has favorite_car_id / favorite_location_id + the four show_* booleans
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
from app import create_app
from app.db import db

app = create_app()
with app.app_context():
    insp = db.inspect(db.engine)
    cols = {c['name'] for c in insp.get_columns('stage_runs')}
    expected = {
        'id', 'username', 'received_at', 'source_type', 'event_id',
        'challenge_id', 'event_index', 'stage_index', 'track_id',
        'track_model_id', 'location_id', 'vehicle_id', 'vehicle_class_id',
        'time_ms', 'player_finished', 'player_end_status', 'race_position',
        'distance_m', 'using_wheel', 'using_assists', 'hardcore_damage',
        'assists_enabled', 'input_library', 'input_device', 'input_name',
        'tyre_compound', 'tyre_wear', 'surface_degradation_enabled',
        'surface_degradation_level', 'weather_preset_id', 'vr_enabled',
        'cam_bonnet_m', 'cam_bumper_m', 'cam_chase_close_m',
        'cam_chase_far_m', 'cam_dash_m', 'cam_head_m', 'g_days_played',
        'g_seconds_played', 'g_session_count',
    }
    missing = expected - cols
    print('--- stage_runs: %d columns, missing=%s' % (len(cols), sorted(missing) or 'none'))
    ucols = {c['name'] for c in insp.get_columns('users')}
    uexpected = {'favorite_car_id', 'favorite_location_id', 'show_device_name',
                 'show_days_played', 'show_seconds_played', 'show_sessions'}
    umissing = uexpected - ucols
    print('--- users profile cols: missing=%s' % (sorted(umissing) or 'none'))
    print('--- stage_runs rows:',
          db.session.execute(db.text('SELECT COUNT(*) FROM stage_runs')).scalar())
EOF
