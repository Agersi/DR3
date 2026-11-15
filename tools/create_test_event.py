#!/usr/bin/env python3
"""Create a test championship in a sandbox club by cloning a donor event's legs.

Usage (VPS):
    /opt/dr3/.venv/bin/python create_test_event.py DONOR_EVENT_ID \
        [--club club-302ec2f1] [--legs N] [--days N] [--start-minutes N] \
        [--name NAME] [--no-hardcore] [--webdir /opt/dr3/web]

The new event is multi-mode (ONE shared window, every leg/stage immediately
drivable), hardcore damage on by default, no unexpected moments / cockpit
forcing / assists. Aborts if the target club already has an active event
overlapping the window (the game shows one championship per club).
"""
import argparse
import copy
import json
import secrets
import sys
from datetime import timedelta


def main() -> int:
    ap = argparse.ArgumentParser(description='clone a donor event into a test club')
    ap.add_argument('donor', help='event id to clone legs from')
    ap.add_argument('--club', default='club-302ec2f1', help='target club id (Test Club)')
    ap.add_argument('--legs', type=int, default=0,
                    help='keep only the first N legs (0 = all)')
    ap.add_argument('--days', type=int, default=0,
                    help='shared window in days (0 = donor first-leg duration)')
    ap.add_argument('--start-minutes', type=int, default=5,
                    help='delay before start (default 5)')
    ap.add_argument('--name', default=None, help='event name (default: target club name)')
    ap.add_argument('--no-hardcore', action='store_true')
    ap.add_argument('--webdir', default='/opt/dr3/web',
                    help='deployed web app dir (added to sys.path)')
    args = ap.parse_args()

    import os
    if not os.environ.get('DATABASE_URL'):
        env_file = os.path.join(args.webdir, '.env')
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        os.environ.setdefault(k.strip(), v.strip())
    sys.path.insert(0, args.webdir)

    from app import create_app
    from app.db import db
    from app.models import Club, Event, utcnow
    from app.tables import bucket_for_duration, championship_duration

    app = create_app()
    with app.app_context():
        donor = db.session.get(Event, args.donor)
        if donor is None:
            print(f'donor event {args.donor} not found')
            return 1
        club = db.session.get(Club, args.club)
        if club is None:
            print(f'club {args.club} not found')
            return 1

        legs = copy.deepcopy(json.loads(donor.legs_json or '[]'))
        if not legs:
            print('donor has no legs')
            return 1
        if args.legs > 0:
            legs = legs[:args.legs]

        # Multi-mode semantics: the first leg carries the shared window, the
        # rest ride on it with zero duration (same as championship_submit).
        first_dur = args.days or int((legs[0].get('duration') or {}).get('days', 0) or 0)
        if first_dur <= 0:
            print('no usable duration (pass --days)')
            return 1
        legs[0]['duration'] = {'days': first_dur, 'hours': 0, 'mins': 0}
        for leg in legs[1:]:
            leg['duration'] = {'days': 0, 'hours': 0, 'mins': 0}

        total = championship_duration(legs)
        start = utcnow() + timedelta(minutes=args.start_minutes)
        end = start + total

        clash = (Event.query
                 .filter(Event.club_id == club.id, Event.active.is_(True),
                         Event.start_time < end, Event.end_time > start)
                 .first())
        if clash is not None:
            print(f'clash: {clash.id} "{clash.name}" runs until {clash.end_time}')
            return 1

        settings = {
            'hardcore_damage': not args.no_hardcore,
            'unexpected_moments': False,
            'force_cockpit_camera': False,
            'allow_assists': False,
            'mode': 'multi',
            'scoring': 'wrc',
            'power_stage': False,
        }
        first = legs[0]
        event = Event(
            id=f'evt-{secrets.token_hex(4)}',
            schema_version=2,
            name=args.name or club.name,
            type=bucket_for_duration(total),
            club_id=club.id,
            start_time=start, end_time=end,
            active=True, featured=False, system=False,
            settings_json=json.dumps(settings),
            location=first.get('location'),
            car_class=first.get('car_class'),
            surface=first.get('surface'),
            conditions=(first.get('stages') or [{}])[0].get('conditions', ''),
            stages_json=json.dumps(first.get('stages', [])),
            legs_json=json.dumps(legs),
        )
        db.session.add(event)
        db.session.commit()

        print(f'created {event.id} "{event.name}" in {club.id} ({club.name})')
        print(f'window: {start} -> {end}  hardcore={settings["hardcore_damage"]}')
        for i, leg in enumerate(legs):
            print(f'leg {i}: {leg.get("location")} class={leg.get("car_class")} '
                  f'stages={len(leg.get("stages", []))}')
            for s in leg.get('stages', []):
                print(f'   - {s.get("name") or s.get("stage_id")} ({s.get("conditions")})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
