#!/bin/bash
# One-off restore of stage_runs 673/674 (Trois-Rivieres RX TT, Agersi): the
# v0.1.70-era catalog repair wrongly collapsed two genuine 2-lap runs to one
# lap. Values below are the EXACT original snapshot values from the local
# EgoNet DataMining captures of 2026-11-14 (dry 05:14:49, wet 05:17:45).
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
from app import create_app
from app.db import db
from app.models import StageRun

FIX = {
    673: (2842, 2841.924072265625),
    674: (2814, 2813.7421875),
}

app = create_app()
with app.app_context():
    for rid, (dist, cam) in FIX.items():
        r = db.session.get(StageRun, rid)
        assert r is not None and r.track_model_id == 565 \
            and r.username == 'Agersi' and r.source_type == 'timetrial', \
            (rid, None if r is None else (r.username, r.track_model_id))
        print(f"restoring id={rid}: dist {r.distance_m} -> {dist}, "
              f"cam_head {r.cam_head_m} -> {cam}")
        r.distance_m = dist
        r.cam_head_m = cam
    db.session.commit()
    print("committed")
EOF
