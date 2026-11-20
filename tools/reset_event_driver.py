#!/usr/bin/env python3
"""Reset a driver's stored progress/result for one club event so they can re-run it.

Deletes the driver's club_results row AND their per-stage club_stage_states rows
for the given event. stage_runs (append-only run history/stats) is left intact -
it does not block re-entry.

Usage (VPS):
    /opt/dr3/.venv/bin/python reset_event_driver.py EVENT_ID [USERNAME] [--apply] \
        [--webdir /opt/dr3/web]

Without --apply this is a DRY RUN: it prints what would be deleted. If USERNAME
is omitted, lists the drivers that currently have data for the event.
"""
import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description='reset a driver\'s club-event progress')
    ap.add_argument('event', help='event id (evt-...)')
    ap.add_argument('username', nargs='?', default=None,
                    help='driver username (omit to list drivers for the event)')
    ap.add_argument('--apply', action='store_true',
                    help='actually delete (default: dry run)')
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
    from app.models import Result, ClubStageState

    app = create_app()
    with app.app_context():
        ev_results = Result.query.filter_by(event_id=args.event).all()
        if not ev_results:
            print(f'no club_results rows for {args.event} - nothing to reset')
            return 0

        if args.username is None:
            print(f'drivers with data for {args.event}:')
            for r in ev_results:
                n_st = ClubStageState.query.filter_by(
                    event_id=args.event, username=r.username).count()
                print(f'  {r.username}: result id={r.id} '
                      f'total_time_ms={r.total_time_ms} stage_states={n_st}')
            print('re-run with a USERNAME to target one driver.')
            return 0

        res = Result.query.filter_by(event_id=args.event,
                                     username=args.username).first()
        if res is None:
            print(f'no club_results row for {args.event} / {args.username}')
            return 0
        n_st = ClubStageState.query.filter_by(
            event_id=args.event, username=args.username).count()
        print(f'{args.event} / {args.username}:')
        print(f'  club_results id={res.id} total_time_ms={res.total_time_ms} '
              f'stages_json_len={len(res.stages_json or "")}')
        print(f'  club_stage_states rows = {n_st}')
        if not args.apply:
            print('DRY RUN - nothing deleted. Re-run with --apply to delete.')
            return 0
        ClubStageState.query.filter_by(event_id=args.event,
                                       username=args.username).delete()
        db.session.delete(res)
        db.session.commit()
        print(f'DELETED: club_results id={res.id} + {n_st} club_stage_states rows')
    return 0


if __name__ == '__main__':
    sys.exit(main())
