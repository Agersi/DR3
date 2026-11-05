#!/bin/bash
# Idempotent repair of stage_runs data-quality issues found in the 2026-11-05
# production audit (first live v0.1.55 rows):
#   - duplicate rows for the same (user, challenge, evt_idx, stage_idx) slot
#     within 120 s — the game retransmits StageComplete and the pre-fix client
#     buffer flushed a half row before the retransmit paired up; keep the more
#     complete row of each cluster
#   - DNF rows (player_end_status != 0) carrying untrusted distance_m -> NULL
#     (close-out snapshots carry stale/cumulative distances)
# The web-side fix (stage-detail dedup + DNF distance drop) prevents both from
# recurring; re-running this script is a no-op once applied. Read/write: only
# the stage_runs table.
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
from datetime import timedelta

from app import create_app
from app.db import db
from app.models import StageRun

# Snapshot-only fields (same list the web endpoint scores with) — used to pick
# the more complete row of a duplicate cluster.
RICH = (
    'track_id', 'track_model_id', 'location_id', 'vehicle_class_id',
    'input_library', 'input_device', 'input_name',
    'cam_bonnet_m', 'cam_bumper_m', 'cam_chase_close_m', 'cam_chase_far_m',
    'cam_dash_m', 'cam_head_m',
    'g_days_played', 'g_seconds_played', 'g_session_count',
)


def score(r):
    n = 0
    for key in RICH:
        v = getattr(r, key)
        if v is None or (isinstance(v, (int, float)) and v == 0):
            continue
        n += 1
    return n


app = create_app()
with app.app_context():
    rows = db.session.query(StageRun).order_by(
        StageRun.received_at, StageRun.id).all()

    deleted = []
    by_slot = {}
    for r in rows:
        if r.challenge_id is None:
            continue
        key = (r.username, r.challenge_id, r.event_index, r.stage_index)
        by_slot.setdefault(key, []).append(r)
    for key, group in by_slot.items():
        clusters = []
        for r in group:
            if clusters and (r.received_at - clusters[-1][-1].received_at) \
                    <= timedelta(seconds=120):
                clusters[-1].append(r)
            else:
                clusters.append([r])
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            keep = max(cluster, key=score)
            for r in cluster:
                if r is not keep:
                    deleted.append((r.id, keep.id))
                    db.session.delete(r)

    nulled = []
    for r in rows:
        if r.player_end_status != 0 and r.distance_m is not None:
            nulled.append(r.id)
            r.distance_m = None

    db.session.commit()
    print('repair_stage_runs: deleted=%s kept=%s dnf_distance_nulled=%s'
          % (deleted or '-', [k for _d, k in deleted] or '-', nulled or '-'))
EOF
