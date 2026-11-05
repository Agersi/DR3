#!/bin/bash
# Compare the stored users.stages_driven / total_distance_m counters (shown on
# /drivers) against the true per-run data: stage_runs rows by source type and
# clean stage entries in results. Divergence explains why /drivers and the
# profile Driving panel disagree. Read-only.
# Usage: check_user_counters.sh [USERNAME] [--dates]
# With --dates, also prints each clean entry's submitted_at + distance so a
# reset point can be located by summing entries after candidate dates.
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
ARGS=()
for a in "$@"; do
    case "$a" in
        --dates) DATES=1 ;;
        *) ARGS+=("$a") ;;
    esac
done
/opt/dr3/.venv/bin/python - "${ARGS[@]:-}" "${DATES:-0}" <<'EOF'
import json
import sys

from app import create_app
from app.db import db
from app.models import Result, StageRun, User

uname = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else ''
show_dates = sys.argv[2] == '1' if len(sys.argv) > 2 else False
app = create_app()
with app.app_context():
    users = (User.query.filter(User.username.like(f'%{uname}%'))
             .all() if uname else User.query.all())
    for u in users:
        runs = StageRun.query.filter_by(username=u.username).all()
        by_src = {}
        dist_by_src = {}
        for r in runs:
            by_src[r.source_type] = by_src.get(r.source_type, 0) + 1
            if r.distance_m is not None:
                dist_by_src[r.source_type] = (
                    dist_by_src.get(r.source_type, 0) + r.distance_m)
        # Clean entries split by whether they carry distance_driven (only a
        # real StageComplete POST sends it; the leaderboard pre-persist does
        # not) — each kind triggers one counter increment on the web side.
        with_dist = without_dist = 0
        dist_sum = 0
        per_event = {}
        dated = []
        for res in Result.query.filter_by(username=u.username).all():
            n = 0
            for p, s in enumerate(json.loads(res.stages_json or '[]')):
                if int(s.get('race_status', 0) or 0) == 0 and int(
                        s.get('time_ms', 0) or 0) > 0:
                    n += 1
                    d = int(s.get('distance_driven') or 0)
                    if d:
                        with_dist += 1
                        dist_sum += d
                    else:
                        without_dist += 1
                    dated.append((s.get('submitted_at') or '?',
                                  res.event_id, p, int(s['time_ms']), d))
            if n:
                per_event[res.event_id] = (n, res.total_time_ms)
        if show_dates:
            for ts, eid, p, tms, d in sorted(dated):
                print(f'  {ts}  {eid} pos={p} time_ms={tms} dist={d}')
        print(f'--- {u.username} ---')
        print(f'stored counters : stages_driven={u.stages_driven} '
              f'total_distance_m={u.total_distance_m}')
        print(f'stage_runs rows : total={len(runs)} by_source={by_src}')
        print(f'  distance by source (m): {dist_by_src}')
        print(f'results clean entries: with_distance_driven={with_dist} '
              f'(sum {dist_sum} m) pre-persist-only={without_dist}')
        for eid, (n, total_ms) in sorted(per_event.items()):
            print(f'  {eid}: {n} clean stages (result total {total_ms} ms)')
EOF
