#!/usr/bin/env python3
"""Audit the car shown on the club-detail events board (read-only).

For one championship round, prints per driver what the Overall board shows —
the Result-level vehicle_id (the car of their latest submission ANYWHERE in
the championship) — next to the cars recorded in that round's stage_runs rows.
[MISMATCH] marks a driver whose Result-level car differs from a car driven in
the round: for those, the board displays the wrong car.

Run on the VPS:
    /opt/dr3/.venv/bin/python check_event_board_car.py EVENT_ID [ROUND_INDEX]
    /opt/dr3/.venv/bin/python check_event_board_car.py --club CLUB_NAME_OR_ID [ROUND_INDEX]
"""
import os
import sys

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, "/opt/dr3/web")
from app import create_app  # noqa: E402
from app.db import db  # noqa: E402
from app.models import Club, Event, Result, StageRun  # noqa: E402
from app.results_view import _now_utc, championship_rounds  # noqa: E402
from app.scoring import vehicle_meta  # noqa: E402

args = sys.argv[1:]
round_index = 0
if args and args[0] == "--club":
    needle = args[1] if len(args) > 1 else ""
    rest = args[2:]
else:
    needle = args[0] if args else ""
    rest = args[1:]
if rest:
    round_index = int(rest[0])

app = create_app()
with app.app_context():
    events = []
    if needle.startswith("evt-"):
        e = db.session.get(Event, needle)
        events = [e] if e else []
    else:
        club = Club.query.filter(
            db.or_(Club.id == needle, Club.name == needle)).first()
        if not club:
            raise SystemExit(f"no club matching {needle!r}")
        events = (Event.query.filter_by(club_id=club.id)
                  .order_by(Event.start_time).all())
    if not events:
        raise SystemExit(f"no event for {needle!r}")

    base = _now_utc()
    for ev in events:
        event = ev.to_dict()
        rounds = championship_rounds(event, base)
        if not rounds:
            print(f"{ev.id}: no rounds")
            continue
        ri = max(0, min(round_index, len(rounds) - 1))
        rnd = rounds[ri]
        offset, count = rnd["offset"], rnd["count"]

        runs_by_slot = {}
        for r in StageRun.query.filter_by(event_id=ev.id).all():
            if r.flat_stage_index is None:
                continue
            key = (r.username, r.flat_stage_index)
            prev = runs_by_slot.get(key)
            if prev is None or (r.received_at or base) >= (prev.received_at or base):
                runs_by_slot[key] = r

        print(f"== {ev.id} | {ev.name} | round {ri + 1}: {rnd['location']} "
              f"class={rnd.get('car_class')} offset={offset} count={count}")
        for e in Result.query.filter_by(event_id=ev.id).order_by(Result.id).all():
            res_vid = e.vehicle_id
            res_name, _ = vehicle_meta(res_vid) if res_vid is not None else (None, "")
            per_stage = []
            mismatch = False
            for k in range(count):
                r = runs_by_slot.get((e.username, offset + k))
                rv = getattr(r, "vehicle_id", None) if r else None
                rn, _ = vehicle_meta(rv) if rv is not None else (None, "")
                per_stage.append(f"S{k + 1}:{rn or '-'}")
                if rv is not None and res_vid is not None and int(rv) != int(res_vid):
                    mismatch = True
            print(f"   {e.username}: board(Result-level)=({res_vid}) {res_name or '?'} | "
                  f"runs: {' '.join(per_stage)}{'  [MISMATCH]' if mismatch else ''}")
