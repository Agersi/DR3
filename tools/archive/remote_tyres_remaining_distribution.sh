#!/usr/bin/env bash
# Read-only live-DB inspection of stored TyresRemaining / TyreCompound values.
#
# Usage:
#   python tools/vps.py --run-script tools/remote_tyres_remaining_distribution.sh
#   python tools/vps.py --run-script tools/remote_tyres_remaining_distribution.sh samples [VALUE]
#   python tools/vps.py --run-script tools/remote_tyres_remaining_distribution.sh trace EVENT_ID USERNAME
set -euo pipefail

MODE="${1:-distribution}"
VALUE="${2:-}"
TRACE_USER="${3:-}"
export MODE VALUE TRACE_USER

set -a
. /opt/dr3/web/.env
set +a
cd /opt/dr3/web

/opt/dr3/.venv/bin/python - <<'PY'
import json
import os
from collections import Counter

from sqlalchemy import create_engine, text

mode = os.environ.get("MODE", "distribution")
value = os.environ.get("VALUE", "")
trace_user = os.environ.get("TRACE_USER", "")

engine = create_engine(os.environ["DATABASE_URL"])

if mode == "trace":
    event_id = value
    query = (
        "SELECT event_id, username, stages_json FROM club_results"
        " WHERE event_id = :event_id AND lower(username) = lower(:username)"
    )
    with engine.connect() as conn:
        rows = conn.execute(
            text(query),
            {"event_id": event_id, "username": trace_user},
        ).fetchall()

    if not rows:
        print(f"no club_results row for event={event_id} user={trace_user}")
        raise SystemExit(0)

    for event_id_row, username, sj in rows:
        try:
            stages = json.loads(sj or "[]")
        except Exception:
            stages = []
        if not isinstance(stages, list):
            continue
        print(f"event={event_id_row} user={username} stages={len(stages)}")
        for pos, s in enumerate(stages):
            if not isinstance(s, dict):
                print(pos, json.dumps({"raw": s}, sort_keys=True))
                continue
            print(json.dumps({
                "pos": pos,
                "stage_index": s.get("stage_index"),
                "time_ms": s.get("time_ms"),
                "race_status": s.get("race_status"),
                "has_repaired": s.get("has_repaired"),
                "vehicle_id": s.get("vehicle_id"),
                "livery_id": s.get("livery_id"),
                "tyre_compound": s.get("tyre_compound"),
                "tyres_remaining": s.get("tyres_remaining"),
            }, sort_keys=True))
    raise SystemExit(0)

if mode == "samples":
    query = (
        "SELECT event_id, username, stages_json FROM club_results"
    )
    with engine.connect() as conn:
        rows = conn.execute(text(query)).fetchall()

    shown = 0
    for event_id, username, sj in rows:
        try:
            stages = json.loads(sj or "[]")
        except Exception:
            continue
        if not isinstance(stages, list):
            continue
        for pos, s in enumerate(stages):
            if not isinstance(s, dict) or "tyres_remaining" not in s:
                continue
            if value and str(s.get("tyres_remaining")) != value:
                continue
            print(json.dumps({
                "pos": pos,
                "event_id": event_id,
                "username": username,
                "stage_index": s.get("stage_index"),
                "time_ms": s.get("time_ms"),
                "race_status": s.get("race_status"),
                "has_repaired": s.get("has_repaired"),
                "vehicle_id": s.get("vehicle_id"),
                "livery_id": s.get("livery_id"),
                "tyre_compound": s.get("tyre_compound"),
                "tyres_remaining": s.get("tyres_remaining"),
            }, sort_keys=True))
            shown += 1
            if shown >= 20:
                break
        if shown >= 20:
            break
    print(f"shown={shown}")
    raise SystemExit(0)

completed = Counter()
in_progress = Counter()
pairs = Counter()

with engine.connect() as conn:
    rows = conn.execute(text(
        "SELECT stages_json, in_progress_json FROM club_results"
    )).fetchall()

for sj, ipj in rows:
    try:
        stages = json.loads(sj or "[]")
    except Exception:
        stages = []
    if isinstance(stages, list):
        for s in stages:
            if isinstance(s, dict) and "tyres_remaining" in s:
                completed[s.get("tyres_remaining")] += 1
                pairs[(s.get("tyre_compound"), s.get("tyres_remaining"))] += 1

    try:
        ip = json.loads(ipj or "{}")
    except Exception:
        ip = {}
    if isinstance(ip, dict) and "tyres_remaining" in ip:
        in_progress[ip.get("tyres_remaining")] += 1


def fmt(counter):
    return dict(sorted(
        counter.items(),
        key=lambda kv: (kv[0] is None, str(kv[0])),
    ))


print("completed", fmt(completed))
print("in_progress", fmt(in_progress))
print("pairs")
for k, v in sorted(
    pairs.items(),
    key=lambda kv: (
        kv[0][0] is None, str(kv[0][0]),
        kv[0][1] is None, str(kv[0][1]),
    ),
):
    print(k[0], k[1], v)
PY
