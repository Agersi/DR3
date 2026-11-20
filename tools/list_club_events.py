#!/usr/bin/env python3
"""List a club's active events with per-stage service-area detail.

Usage (VPS):
    /opt/dr3/.venv/bin/python list_club_events.py [CLUB_ID] [--webdir /opt/dr3/web]

Prints each active event's id, name, window and every stage with its
service_area value ('None' = no service). Useful for picking a test event.
"""
import argparse
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description='list a club\'s active events')
    ap.add_argument('club', nargs='?', default='club-302ec2f1', help='club id')
    ap.add_argument('--webdir', default='/opt/dr3/web',
                    help='deployed web app dir (added to sys.path)')
    args = ap.parse_args()

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
    from app.models import Event

    app = create_app()
    with app.app_context():
        evs = (Event.query.filter_by(club_id=args.club, active=True)
               .order_by(Event.start_time).all())
        if not evs:
            print(f'no active events for {args.club}')
            return 0
        for e in evs:
            legs = json.loads(e.legs_json or '[]')
            n_stages = sum(len(leg.get('stages', [])) for leg in legs)
            svc = sum(1 for leg in legs for s in leg.get('stages', [])
                      if (s.get('service_area') or 'None') != 'None')
            print(f'{e.id}  "{e.name}"  {e.start_time} -> {e.end_time}')
            print(f'   legs={len(legs)} stages={n_stages} service_areas={svc}')
            for li, leg in enumerate(legs):
                for si, s in enumerate(leg.get('stages', [])):
                    name = s.get('name') or s.get('stage_id') or '?'
                    print(f'   leg{li} stage{si}: {name} '
                          f'service={s.get("service_area") or "None"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
