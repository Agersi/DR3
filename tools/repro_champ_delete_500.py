"""Repro: 500 on GET /clubs/<club>/championship/<event>/delete for a LIVE
multi-location championship with posted results (Test Club evt-3af78912).

Builds a throwaway SQLite fixture mirroring the live event (2 legs, hardcore,
entries with club_stage_states heavy payloads), then hits the delete-confirm
page as the club owner with exception propagation on so the real traceback
prints.

Run:  tools\\..\\dr3\\.venv-build\\Scripts\\python.exe tools/repro_champ_delete_500.py
      (any python with dr3/web deps; uses dr3/web/.venv by default)
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'dr3' / 'web'
DB_PATH = WEB / 'repro_champ_delete.db'
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ['SECRET_KEY'] = 'repro-key'
os.environ['DATABASE_URL'] = f'sqlite:///{DB_PATH}'
os.environ['DR3_DISABLE_CSRF'] = '1'
os.environ.pop('SESSION_COOKIE_SECURE', None)

sys.path.insert(0, str(WEB))

CLUB_ID = 'club-repro'
EVENT_ID = 'evt-repro01'


def main() -> int:
    from app import create_app
    from app.auth import create_user
    from app.db import db
    from app.models import Club, Event, Result, ClubStageState, utcnow

    app = create_app()
    app.testing = True  # propagate exceptions instead of 500 pages

    with app.app_context():
        owner = create_user('reproowner', 'repro@example.com', 'password123',
                            email_verified=True)
        club = Club(id=CLUB_ID, name='Test Club', description='',
                    visibility='public', join_policy='open', created_by=owner.id)
        db.session.add(club)
        db.session.flush()
        from app.clubs import add_member
        add_member(club, owner)

        # Live multi-location championship: Spain + Poland, 3 stages each,
        # F2 KitCar, hardcore — structurally identical to evt-3af78912.
        def stage(tid: int, cid: int, name: str, km: float) -> dict:
            return {'name': name, 'track_id': tid, 'distance_km': km,
                    'conditions_id': cid, 'conditions': 'Dry',
                    'surface_deg': 'Medium', 'service_area': 'Medium'}

        legs = [
            {'location': 'Spain', 'car_class': 'F2 KitCar', 'surface': 'Asphalt',
             'duration': {'days': 1, 'hours': 0, 'mins': 0}, 'rx_laps': None,
             'stages': [stage(1, 1, 'RC1 Sample', 4.3), stage(2, 1, 'RC2 Sample', 5.1),
                        stage(3, 2, 'RC3 Sample', 3.9)]},
            {'location': 'Poland', 'car_class': 'F2 KitCar', 'surface': 'Gravel',
             'duration': {'days': 0, 'hours': 0, 'mins': 0}, 'rx_laps': None,
             'stages': [stage(1, 1, 'RC1 Sample', 4.8), stage(2, 1, 'RC2 Sample', 6.2),
                        stage(3, 2, 'RC3 Sample', 5.5)]},
        ]
        now = utcnow()
        db.session.add(Event(
            id=EVENT_ID, schema_version=2, name='Test Club', type='daily',
            location='Spain', car_class='F2 KitCar', surface='Asphalt',
            conditions='Dry', stages_json=json.dumps(legs[0]['stages']),
            settings_json=json.dumps({'hardcore_damage': True, 'unexpected_moments': False,
                                      'force_cockpit_camera': False, 'allow_assists': True,
                                      'mode': 'multi', 'scoring': 'wrc',
                                      'power_stage': False}),
            legs_json=json.dumps(legs),
            start_time=now - timedelta(hours=2), end_time=now + timedelta(hours=26),
            active=True, featured=False, club_id=CLUB_ID, system=False,
        ))

        # One driver with a completed stage 1 (left-side hit) — heavy payloads
        # in club_stage_states, exactly what the live event carries.
        stages = [{
            'stage_index': 0, 'event_index': 0, 'time_ms': 950000,
            'vehicle_id': 469, 'livery_id': 7, 'nationality_id': 31,
            'comp_damage': {'wheels_wear': [0.2, 0.1, 0.1, 0.05],
                            'wheels_impact': [0.4, 0.0, 0.0, 0.7],
                            'springs': [0.1, 0.0, 0.2, 0.0],
                            'brakes': [0.1, 0.1, 0.1, 0.1],
                            'dampers': [0.0, 0.0, 0.3, 0.0],
                            'bodywork': [0.1, 0.5, 0.2, 0.3],
                            'gearbox': 0.0, 'turbo': 0.0,
                            'diff_wear': 0.0, 'diff_impact': 0.9,
                            'quick_repairs': []},
        }]
        db.session.add(Result(event_id=EVENT_ID, username='reprodriver',
                              car='KitCar F2', vehicle_id=469,
                              total_time_ms=950000, attempts_used=1,
                              stages_json=json.dumps(stages)))
        db.session.add(ClubStageState(
            event_id=EVENT_ID, username='reprodriver', flat_stage_index=0,
            tuning_setup_b64='aGVsbG8=',
            comp_damage_json=json.dumps(stages[0]['comp_damage']),
            vehicle_mud_json=json.dumps({'mud': 0.1})))
        db.session.commit()

    client = app.test_client()
    r = client.post('/login', data={'username': 'reproowner', 'password': 'password123'},
                    follow_redirects=False)
    print(f'login: {r.status_code}')

    # Sanity: the club detail board (which renders this event's entries).
    r = client.get(f'/clubs/{CLUB_ID}')
    print(f'club detail: {r.status_code}')

    try:
        r = client.get(f'/clubs/{CLUB_ID}/championship/{EVENT_ID}/delete')
        print(f'delete confirm: {r.status_code}')
        if r.status_code != 200:
            print(r.get_data(as_text=True)[:2000])
    except Exception:
        print('delete confirm raised:')
        traceback.print_exc()
        return 1
    print('OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
