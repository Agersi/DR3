"""End-to-end smoke test: register -> verify -> login -> token -> game API -> cron.

Run:  .venv\\Scripts\\python.exe smoke_test.py   (from dr3/web)
Uses a throwaway SQLite DB; wipes it first.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / 'smoke.db'
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ['SECRET_KEY'] = 'smoke-test-key'
os.environ['DATABASE_URL'] = f'sqlite:///{DB_PATH}'
os.environ['CRON_API_KEY'] = 'smoke-cron-key'
os.environ['DR3_DISABLE_CSRF'] = '1'
os.environ.pop('SESSION_COOKIE_SECURE', None)

sys.path.insert(0, str(HERE))

from app import create_app  # noqa: E402
from app.db import db  # noqa: E402
from app.models import User  # noqa: E402


def main() -> int:
    app = create_app()
    client = app.test_client()
    failures: list[str] = []

    def check(name: str, cond: bool, extra: str = '') -> None:
        status = 'ok' if cond else 'FAIL'
        print(f'[{status}] {name}' + (f' — {extra}' if extra and not cond else ''))
        if not cond:
            failures.append(name)

    # 1. register
    r = client.post('/register', data={
        'username': 'smokedriver', 'email': 'smoke@example.com',
        'password': 'password123', 'confirm': 'password123',
        'display_name': 'Smoke Driver', 'country': 'Sweden',
    }, follow_redirects=False)
    check('register redirects to login', r.status_code == 302, f'status={r.status_code}')

    # 1b. register page offers a country select menu (backlog #40)
    r = client.get('/register')
    reg_html = r.get_data(as_text=True)
    check('register page has country select menu',
          '<select id="country"' in reg_html
          and 'value="Denmark"' in reg_html and 'value="United States"' in reg_html
          and 'value="Wales"' in reg_html,
          f'status={r.status_code}')

    with app.app_context():
        user = User.query.filter_by(username='smokedriver').first()
        check('user row created', user is not None)
        verify_token = user.verify_token if user else ''

    # 2. verify email
    r = client.get(f'/verify_email/{verify_token}', follow_redirects=True)
    check('verify email lands on dashboard', '/dashboard' in r.request.path, r.request.path)

    # 3. login (fresh session)
    client.get('/logout')
    r = client.post('/login', data={'username': 'smokedriver', 'password': 'password123'},
                    follow_redirects=False)
    check('login redirects to dashboard', r.status_code == 302 and '/dashboard' in r.headers.get('Location', ''),
          f"loc={r.headers.get('Location')}")

    # 3b. account page: country select preselects the stored value (backlog #40)
    r = client.get('/account')
    acc_html = r.get_data(as_text=True)
    check('account page country select preselects current country',
          '<option value="Sweden" selected' in acc_html, f'status={r.status_code}')

    r = client.get('/profile/smokedriver')
    prof_html = r.get_data(as_text=True)
    check('profile page shows country flag',
          'sweden-flag.png' in prof_html and 'Sweden' in prof_html,
          f'status={r.status_code}')

    # 4. generate game token
    r = client.post('/api/token/generate', follow_redirects=False)
    check('token generate redirects', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        u = User.query.filter_by(username='smokedriver').first()
        from app.models import GameToken
        gt = GameToken.query.filter_by(user_id=u.id, revoked_at=None).first()
        token = gt.token if gt else ''
    check('token issued with df_ prefix', token.startswith('df_'), token[:8])

    headers = {'Authorization': f'Bearer {token}'}

    # 5. game API: token-test, profile
    r = client.get('/api/game/token-test', headers=headers)
    j = r.get_json() or {}
    check('token-test ok', j.get('ok') is True and j.get('username') == 'smokedriver', str(j))

    r = client.get('/api/game/profile', headers=headers)
    j = r.get_json() or {}
    check('profile ok + currency seeded',
          j.get('ok') is True and j.get('soft_currency') == 500000 and j.get('garage_slots') == 8, str(j))

    # 6. cron no longer generates official dailies/weeklies/monthlies
    r = client.post('/api/cron', headers={'X-Cron-Key': 'smoke-cron-key'})
    j = r.get_json() or {}
    check('cron ok + no generated events',
          j.get('ok') is True and j.get('created') == [], str(j))

    # 7. stage API on a temporary non-system event
    with app.app_context():
        from datetime import timedelta
        from app.models import Event, utcnow
        now = utcnow()
        stage = {
            'name': 'Smoke Stage', 'track_id': 10, 'distance_km': 10.0,
            'conditions_id': 5, 'conditions': 'Wet',
            'surface_deg': 'Medium', 'service_area': 'Medium',
        }
        db.session.add(Event(
            id='smoke-stage-event', schema_version=2, name='Smoke Stage Event',
            type='daily', location='Lydden Hill', car_class='Gravel 1',
            surface='Gravel', conditions='Wet',
            stages_json=json.dumps([stage]), settings_json=json.dumps({}),
            legs_json=json.dumps([{
                'location': 'Lydden Hill', 'car_class': 'Gravel 1',
                'surface': 'Gravel', 'duration': {'days': 0, 'hours': 1, 'mins': 0},
                'stages': [stage],
            }]),
            start_time=now - timedelta(minutes=5), end_time=now + timedelta(hours=1),
            active=True, featured=False, club_id=None, system=False,
        ))
        db.session.commit()

    eid = 'smoke-stage-event'
    r = client.post('/api/game/stage-begin', headers=headers, json={
        'event_id': eid, 'stage_index': 0, 'event_index': 0,
        'livery_id': 1, 'tuning_setup_b64': 'dHVuaW5n', 'tyre_compound': 2,
        'tyres_remaining': 3, 'nationality_id': 1, 'vehicle_id': 1,
    })
    check('stage-begin ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))

    r = client.post('/api/game/stage-complete', headers=headers, json={
        'event_id': eid, 'username': 'smokedriver', 'stage_index': 0,
        'event_index': 0, 'time_ms': 421234, 'penalties_ms': 5000,
        'vehicle_id': 1, 'race_status': 0,
    })
    j = r.get_json() or {}
    check('stage-complete ok + position 1',
          j.get('ok') is True and j.get('position') == 1, str(j))

    # partial re-submit must not clobber tuning from stage-begin
    r = client.post('/api/game/stage-complete', headers=headers, json={
        'event_id': eid, 'username': 'smokedriver', 'stage_index': 0,
        'event_index': 0, 'time_ms': 419000,
    })
    j = r.get_json() or {}
    check('partial stage-complete ok', j.get('ok') is True, str(j))

    r = client.get(f'/api/game/leaderboard/{eid}', headers=headers)
    j = r.get_json() or {}
    e0 = (j.get('entries') or [{}])[0]
    st0 = (e0.get('stages') or [{}])[0]
    check('leaderboard: tuning preserved from stage-begin',
          st0.get('tuning_setup_b64') == 'dHVuaW5n', str(st0))
    check('leaderboard: best time kept (419000+5000=424000)',
          e0.get('total_time_ms') == 424000, str(e0.get('total_time_ms')))
    check('leaderboard: entry-level nationality_id from stage-begin',
          e0.get('nationality_id') == 1, str(e0.get('nationality_id')))

    # home recent-times rows carry the driver's flag url (backlog #40)
    r = client.get('/api/home/recent-times?offset=0&limit=25')
    j = r.get_json() or {}
    rows = j.get('rows') or []
    check('home recent-times row carries driver flag url',
          any((row.get('driver_flag') or '').endswith('sweden-flag.png') for row in rows),
          str(rows)[:200])

    r = client.get('/api/game/my-progress', headers=headers)
    j = r.get_json() or {}
    check('my-progress has event', any(x['event_id'] == eid for x in j.get('events', [])),
          str(j)[:200])

    # 7a. DNF marker semantics (backlog #32): a pre-persisted time-limit row
    # picks up its vehicle from the stage-begin capture, and the DNF close-out
    # marker flags the entry without changing its time or burning an attempt.
    with app.app_context():
        from datetime import timedelta
        from app.models import Event, utcnow
        now = utcnow()
        st = {'name': 'Smoke DNF Stage', 'track_id': 10, 'distance_km': 10.0,
              'conditions_id': 5, 'conditions': 'Dry', 'surface_deg': 'Medium',
              'service_area': 'Medium'}
        db.session.add(Event(
            id='smoke-dnf-event', schema_version=2, name='Smoke DNF Event',
            type='daily', location='Lydden Hill', car_class='Gravel 1',
            surface='Gravel', conditions='Dry',
            stages_json=json.dumps([st, st]), settings_json=json.dumps({}),
            legs_json=json.dumps([{
                'location': 'Lydden Hill', 'car_class': 'Gravel 1',
                'surface': 'Gravel', 'duration': {'days': 0, 'hours': 1, 'mins': 0},
                'stages': [st, st],
            }]),
            start_time=now - timedelta(minutes=5), end_time=now + timedelta(hours=1),
            active=True, featured=False, club_id=None, system=False,
        ))
        db.session.commit()

    eid2 = 'smoke-dnf-event'
    r = client.post('/api/game/stage-begin', headers=headers, json={
        'event_id': eid2, 'stage_index': 1, 'event_index': 0,
        'livery_id': 7, 'tuning_setup_b64': 'ZG5mbWFyaw==', 'tyre_compound': 9,
        'tyres_remaining': 1, 'nationality_id': 31, 'vehicle_id': 469,
    })
    check('dnf: stage-begin ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))

    # pre-persist (leaderboard-view shape): time only, no vehicle of its own
    r = client.post('/api/game/stage-complete', headers=headers, json={
        'event_id': eid2, 'username': 'smokedriver', 'stage_index': 1,
        'event_index': 0, 'time_ms': 900000,
    })
    check('dnf: pre-persist ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))

    r = client.get(f'/api/game/leaderboard/{eid2}', headers=headers)
    j = r.get_json() or {}
    e0 = (j.get('entries') or [{}])[0]
    st1 = (e0.get('stages') or [None, {}])[1]
    check('dnf: pre-persist row carries vehicle from stage-begin',
          e0.get('vehicle_id') == 469 and st1.get('vehicle_id') == 469, str(e0)[:300])

    # DNF close-out marker: flags the entry, keeps the limit time, no attempt
    r = client.post('/api/game/stage-complete', headers=headers, json={
        'event_id': eid2, 'username': 'smokedriver', 'stage_index': 1,
        'event_index': 0, 'time_ms': 0, 'race_status': 4, 'mark_dnf': True,
    })
    check('dnf: marker ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))

    r = client.get(f'/api/game/leaderboard/{eid2}', headers=headers)
    j = r.get_json() or {}
    e0 = (j.get('entries') or [{}])[0]
    st1 = (e0.get('stages') or [None, {}])[1]
    check('dnf: marker flags race_status=4', st1.get('race_status') == 4, str(st1)[:200])
    check('dnf: marker keeps the pre-persisted limit time',
          e0.get('total_time_ms') == 900000, str(e0.get('total_time_ms')))

    r = client.get('/api/game/my-progress', headers=headers)
    j = r.get_json() or {}
    ev2 = next((x for x in j.get('events', []) if x['event_id'] == eid2), {})
    check('dnf: marker burns no attempt', ev2.get('attempts_used') == 0, str(ev2)[:200])

    # scoring: the flagged stage doesn't count as done -> DNF for that rally
    with app.app_context():
        from app import scoring
        from app.events import entries_for_event, get_event
        ev = get_event(eid2)
        rows = scoring.rally_standings(entries_for_event(eid2), {
            'offset': 0, 'count': 2, 'location': ev['location'], 'stages': ev['stages']})
        row = next(rw for rw in rows if rw['username'] == 'smokedriver')
        # Both stages are unfinished: stage 1 is the flagged DNF (it still
        # carries the limit time but race_status=4), stage 0 was never started.
        check('dnf: rally_standings counts the DNF stage as unfinished',
              row['dnf_stages'] == 2, str(row)[:200])

    # 7a-b. Mid-leg abandon (backlog #32, 2026-10-28): wreck on stage 0 -> the
    # pre-persist stores the limit there; the close-out lands on the NEXT
    # stage (rs=5) carrying dnf_stage_index=0, so BOTH entries end up flagged.
    with app.app_context():
        from datetime import timedelta
        from app.models import Event, utcnow
        now = utcnow()
        db.session.add(Event(
            id='smoke-dnf-midleg', schema_version=2, name='Smoke DNF MidLeg',
            type='daily', location='Lydden Hill', car_class='Gravel 1',
            surface='Gravel', conditions='Dry',
            stages_json=json.dumps([st, st]), settings_json=json.dumps({}),
            legs_json=json.dumps([{
                'location': 'Lydden Hill', 'car_class': 'Gravel 1',
                'surface': 'Gravel', 'duration': {'days': 0, 'hours': 1, 'mins': 0},
                'stages': [st, st],
            }]),
            start_time=now - timedelta(minutes=5), end_time=now + timedelta(hours=1),
            active=True, featured=False, club_id=None, system=False,
        ))
        db.session.commit()

    eid3 = 'smoke-dnf-midleg'
    r = client.post('/api/game/stage-begin', headers=headers, json={
        'event_id': eid3, 'stage_index': 0, 'event_index': 0,
        'livery_id': 7, 'tuning_setup_b64': 'ZG5mbWFyaw==', 'tyre_compound': 9,
        'tyres_remaining': 1, 'nationality_id': 31, 'vehicle_id': 469,
    })
    check('midleg: stage-begin ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))

    r = client.post('/api/game/stage-complete', headers=headers, json={
        'event_id': eid3, 'username': 'smokedriver', 'stage_index': 0,
        'event_index': 0, 'time_ms': 900000,
    })
    check('midleg: pre-persist ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))

    r = client.post('/api/game/stage-complete', headers=headers, json={
        'event_id': eid3, 'username': 'smokedriver', 'stage_index': 1,
        'event_index': 0, 'time_ms': 0, 'race_status': 5, 'mark_dnf': True,
        'dnf_stage_index': 0,
    })
    check('midleg: marker ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))

    r = client.get(f'/api/game/leaderboard/{eid3}', headers=headers)
    j = r.get_json() or {}
    e0 = (j.get('entries') or [{}])[0]
    st0 = (e0.get('stages') or [None, None])[0] or {}
    st1 = (e0.get('stages') or [None, None])[1] or {}
    check('midleg: wrecked stage flagged DNF', st0.get('race_status') == 5, str(st0)[:200])
    check('midleg: wrecked stage keeps the pre-persisted limit time',
          st0.get('time_ms') == 900000, str(st0)[:200])
    check('midleg: closed-out stage flagged DNF', st1.get('race_status') == 5, str(st1)[:200])
    check('midleg: total keeps the limit value only once',
          e0.get('total_time_ms') == 900000, str(e0.get('total_time_ms')))

    with app.app_context():
        from app import scoring
        from app.events import entries_for_event, get_event
        ev3 = get_event(eid3)
        rows = scoring.rally_standings(entries_for_event(eid3), {
            'offset': 0, 'count': 2, 'location': ev3['location'], 'stages': ev3['stages']})
        row = next(rw for rw in rows if rw['username'] == 'smokedriver')
        check('midleg: both stages count as unfinished', row['dnf_stages'] == 2, str(row)[:200])

    # 7c. Service-area auto-advance (backlog #32, step 3): the marker carries
    # dnf_stages — every skipped stage from the wreck up to the next service
    # area with its max time. The web flags each one and fills in the limit
    # where no pre-persisted value exists, so my-progress's count-based resume
    # lands right after the last flagged stage. Monte Carlo limits: 450/451 =
    # 900s, 454 = 1800s; service area sits after stage 1.
    with app.app_context():
        from datetime import timedelta
        from app.models import Event, utcnow
        now = utcnow()
        s0 = {'name': 'Skip S0', 'track_id': 450, 'distance_km': 5.17,
              'conditions_id': 5, 'conditions': 'Dry', 'surface_deg': 'Medium',
              'service_area': 'None'}
        s1 = {'name': 'Skip S1', 'track_id': 451, 'distance_km': 5.13,
              'conditions_id': 5, 'conditions': 'Dry', 'surface_deg': 'Medium',
              'service_area': 'Medium'}
        s2 = {'name': 'Skip S2', 'track_id': 454, 'distance_km': 11.27,
              'conditions_id': 5, 'conditions': 'Dry', 'surface_deg': 'Medium',
              'service_area': 'None'}
        db.session.add(Event(
            id='smoke-dnf-skip', schema_version=2, name='Smoke DNF Skip',
            type='daily', location='Monte Carlo', car_class='Gravel 1',
            surface='Asphalt', conditions='Dry',
            stages_json=json.dumps([s0, s1, s2]), settings_json=json.dumps({}),
            legs_json=json.dumps([{
                'location': 'Monte Carlo', 'car_class': 'Gravel 1',
                'surface': 'Asphalt', 'duration': {'days': 0, 'hours': 1, 'mins': 0},
                'stages': [s0, s1, s2],
            }]),
            start_time=now - timedelta(minutes=5), end_time=now + timedelta(hours=1),
            active=True, featured=False, club_id=None, system=False,
        ))
        db.session.commit()

    eid4 = 'smoke-dnf-skip'
    r = client.post('/api/game/stage-begin', headers=headers, json={
        'event_id': eid4, 'stage_index': 0, 'event_index': 0,
        'livery_id': 7, 'tuning_setup_b64': 'ZG5mbWFyaw==', 'tyre_compound': 9,
        'tyres_remaining': 1, 'nationality_id': 31, 'vehicle_id': 469,
    })
    check('skip: stage-begin ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))

    # post-wreck pre-persist of the wrecked stage (stage 0) at its limit
    r = client.post('/api/game/stage-complete', headers=headers, json={
        'event_id': eid4, 'username': 'smokedriver', 'stage_index': 0,
        'event_index': 0, 'time_ms': 900000,
    })
    check('skip: pre-persist ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))

    # close-out on the next stage (rs=5) with the dispatcher's skip plan
    r = client.post('/api/game/stage-complete', headers=headers, json={
        'event_id': eid4, 'username': 'smokedriver', 'stage_index': 1,
        'event_index': 0, 'time_ms': 0, 'race_status': 5, 'mark_dnf': True,
        'dnf_stage_index': 0,
        'dnf_stages': [
            {'stage_index': 0, 'time_ms': 900000},
            {'stage_index': 1, 'time_ms': 900000},
        ],
    })
    check('skip: marker ok', (r.get_json() or {}).get('ok') is True, str(r.get_json()))

    r = client.get(f'/api/game/leaderboard/{eid4}', headers=headers)
    j = r.get_json() or {}
    e0 = (j.get('entries') or [{}])[0]
    sts = e0.get('stages') or []
    st0 = sts[0] if len(sts) > 0 else {}
    st1 = sts[1] if len(sts) > 1 else {}
    st2 = sts[2] if len(sts) > 2 else {}
    check('skip: wrecked stage flagged DNF', st0.get('race_status') == 5, str(st0)[:200])
    check('skip: wrecked stage keeps the pre-persisted limit time',
          st0.get('time_ms') == 900000, str(st0)[:200])
    check('skip: closed-out stage flagged DNF', st1.get('race_status') == 5, str(st1)[:200])
    check('skip: closed-out stage filled with its limit (no pre-persist)',
          st1.get('time_ms') == 900000, str(st1)[:200])
    check('skip: resume stage left untouched',
          st2.get('time_ms', 0) == 0 and 'race_status' not in st2, str(st2)[:200])
    check('skip: total sums both limits once each',
          e0.get('total_time_ms') == 1800000, str(e0.get('total_time_ms')))

    r = client.get('/api/game/my-progress', headers=headers)
    j = r.get_json() or {}
    ev4 = next((x for x in j.get('events', []) if x['event_id'] == eid4), {})
    done = [s['stage_index'] for s in ev4.get('completed_stages', [])]
    check('skip: my-progress counts both flagged stages (resume at stage 2)',
          sorted(done) == [0, 1], str(ev4)[:300])

    with app.app_context():
        from app import scoring
        from app.events import entries_for_event, get_event
        ev4db = get_event(eid4)
        rows = scoring.rally_standings(entries_for_event(eid4), {
            'offset': 0, 'count': 3, 'location': ev4db['location'],
            'stages': ev4db['stages']})
        row = next(rw for rw in rows if rw['username'] == 'smokedriver')
        check('skip: all three stages count as unfinished (2 DNF + resume stage)',
              row['dnf_stages'] == 3, str(row)[:200])

    # clean up so later recent-times checks see only the original single row
    with app.app_context():
        from app.models import Event, Result
        for res in Result.query.filter(
                Result.event_id.in_([eid2, eid3, eid4])).all():
            db.session.delete(res)
        db.session.query(Event).filter(
            Event.id.in_([eid2, eid3, eid4])).delete()
        db.session.commit()

    # 7b. client-version endpoint (source of the client's update banner)
    ver_file = HERE.parent / 'dist' / 'VERSION.txt'
    had_ver = ver_file.is_file()
    saved_ver = ver_file.read_text(encoding='utf-8') if had_ver else None
    try:
        if had_ver:
            ver_file.unlink()
        r = client.get('/api/client-version')
        check('client-version 404 without VERSION.txt',
              r.status_code == 404, f'status={r.status_code}')
        ver_file.parent.mkdir(parents=True, exist_ok=True)
        ver_file.write_text('9.9.9\n', encoding='utf-8')
        r = client.get('/api/client-version')
        j = r.get_json() or {}
        check('client-version serves published version + download link',
              r.status_code == 200 and j.get('version') == '9.9.9'
              and str(j.get('download_url', '')).endswith('/download/DR3-windows.zip'),
              str(j))
    finally:
        if had_ver:
            ver_file.write_text(saved_ver, encoding='utf-8')
        elif ver_file.is_file():
            ver_file.unlink()

    # 7c. home recent-times endpoint (front page infinite-scroll source)
    r = client.get('/api/home/recent-times?offset=0&limit=25')
    j = r.get_json() or {}
    rows = j.get('rows') or []
    check('recent-times returns the posted stage time',
          r.status_code == 200 and len(rows) == 1
          and rows[0].get('driver') == 'Smoke Driver'
          and rows[0].get('time_text')
          and rows[0].get('at_text'), str(j)[:300])
    r = client.get('/api/home/recent-times?offset=1&limit=25')
    j = r.get_json() or {}
    check('recent-times short page signals end of list',
          (j.get('rows') or []) == [], str(j))

    # 8. local time-trial endpoints are removed
    r = client.post('/api/game/time-trial-submit', headers=headers, json={})
    check('tt submit endpoint removed', r.status_code == 404, f'status={r.status_code}')
    r = client.get('/api/game/time-trial-leaderboard?vclass=101&track=10&conditions=5', headers=headers)
    check('tt leaderboard endpoint removed', r.status_code == 404, f'status={r.status_code}')
    r = client.get('/api/game/time-trial-leaderboard-id?vclass=101&track=10&conditions=5&category=1', headers=headers)
    check('tt leaderboard-id endpoint removed', r.status_code == 404, f'status={r.status_code}')

    # 10. game auth (steam link)
    r = client.post('/api/game/auth', json={'steam_name': 'Smoke Driver', 'account_id': 777})
    j = r.get_json() or {}
    check('game auth links by display name', j.get('ok') is True and j.get('linked') is True, str(j))

    # 11. bad token rejected
    r = client.get('/api/game/token-test', headers={'Authorization': 'Bearer df_bad'})
    check('bad token 401', r.status_code == 401, f'status={r.status_code}')

    # ── Clubs flow (client is logged in as smokedriver) ───────────────
    from app.models import Club, ClubMember, Event

    # 12. create a club
    r = client.post('/clubs', data={
        'name': 'Smoke Crew', 'description': 'test club',
        'visibility': 'public', 'join_policy': 'approval',
    }, follow_redirects=False)
    check('create club redirects to detail', r.status_code == 302 and '/clubs/club-' in r.headers.get('Location', ''),
          f"loc={r.headers.get('Location')}")
    with app.app_context():
        club = Club.query.filter_by(name='Smoke Crew').first()
        check('club row created + owner is member',
              club is not None and any(m.user_id == user.id for m in club.members), '')
        club_id = club.id if club else ''

    r = client.get(f'/clubs/{club_id}')
    check('club detail renders for owner', r.status_code == 200 and b'Smoke Crew' in r.data, f'status={r.status_code}')

    # 13. second user registers, verifies, requests to join
    client2 = app.test_client()
    client2.post('/register', data={
        'username': 'secondpilot', 'email': 'second@example.com',
        'password': 'password123', 'confirm': 'password123',
        'display_name': 'Second Pilot', 'country': 'Finland',
    })
    with app.app_context():
        u2 = User.query.filter_by(username='secondpilot').first()
        v2 = u2.verify_token if u2 else ''
    client2.get(f'/verify_email/{v2}', follow_redirects=True)

    r = client2.get(f'/clubs/{club_id}')
    check('club detail visible to non-member (public)', r.status_code == 200, f'status={r.status_code}')

    r = client2.post(f'/clubs/{club_id}/request', follow_redirects=False)
    check('join request enqueued', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        club = db.session.get(Club, club_id)
        reqs = json.loads(club.pending_requests_json or '[]')
        check('pending request stored', any(x.get('username') == 'secondpilot' for x in reqs), str(reqs))

    # 14. owner approves the request
    r = client.post(f'/clubs/{club_id}/requests/secondpilot/approve', follow_redirects=False)
    check('approve redirects', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        m = ClubMember.query.filter_by(club_id=club_id, user_id=u2.id).first()
        check('approved user is now a member', m is not None, '')

    # 15. promote then demote an admin (owner only)
    r = client.post(f'/clubs/{club_id}/admins/secondpilot/promote', follow_redirects=False)
    with app.app_context():
        club = db.session.get(Club, club_id)
        admins = json.loads(club.admins_json or '[]')
        check('promote adds admin', 'secondpilot' in admins, str(admins))
    r = client.post(f'/clubs/{club_id}/admins/secondpilot/demote', follow_redirects=False)
    with app.app_context():
        club = db.session.get(Club, club_id)
        admins = json.loads(club.admins_json or '[]')
        check('demote removes admin', 'secondpilot' not in admins, str(admins))

    # 16. invite by username + accept
    client3 = app.test_client()
    client3.post('/register', data={
        'username': 'invitee', 'email': 'invitee@example.com',
        'password': 'password123', 'confirm': 'password123',
        'display_name': 'Invitee', 'country': 'Estonia',
    })
    with app.app_context():
        u3 = User.query.filter_by(username='invitee').first()
        v3 = u3.verify_token if u3 else ''
    client3.get(f'/verify_email/{v3}', follow_redirects=True)

    r = client.post(f'/clubs/{club_id}/invite', data={'username': 'invitee'}, follow_redirects=False)
    with app.app_context():
        club = db.session.get(Club, club_id)
        invs = json.loads(club.invites_json or '[]')
        check('invite stored', any(i.get('username') == 'invitee' for i in invs), str(invs))

    r = client3.post(f'/clubs/{club_id}/invite/accept', follow_redirects=False)
    with app.app_context():
        m3 = ClubMember.query.filter_by(club_id=club_id, user_id=u3.id).first()
        check('invite accepted -> member', m3 is not None, '')

    # 17. invite link (shareable) flow
    r = client.post(f'/clubs/{club_id}/invite-link', follow_redirects=False)
    with app.app_context():
        from app.models import ClubInvite
        link = ClubInvite.query.filter_by(club_id=club_id).first()
        link_token = link.token if link else ''
    check('invite link created', bool(link_token), '')

    client4 = app.test_client()
    client4.post('/register', data={
        'username': 'linkjoiner', 'email': 'link@example.com',
        'password': 'password123', 'confirm': 'password123',
        'display_name': 'Link Joiner', 'country': 'Latvia',
    })
    with app.app_context():
        u4 = User.query.filter_by(username='linkjoiner').first()
        v4 = u4.verify_token if u4 else ''
    client4.get(f'/verify_email/{v4}', follow_redirects=True)

    r = client4.get(f'/clubs/{club_id}/join/{link_token}')
    check('invite landing renders for logged-in user', r.status_code == 200 and b'Accept invite' in r.data, f'status={r.status_code}')
    r = client4.post(f'/clubs/{club_id}/join/{link_token}', follow_redirects=False)
    with app.app_context():
        m4 = ClubMember.query.filter_by(club_id=club_id, user_id=u4.id).first()
        check('invite link join -> member', m4 is not None, '')

    # 18. pick a resolvable location/class for the championship tests
    from app.tables import (
        STAGES, CAR_CLASSES, RX_CAR_CLASSES, CONFIRMED_VEHICLE_CLASS_IDS,
        STAGE_ROUTES, stage_conditions_for_location, vehicle_class_id_for_label,
    )
    qloc = next(
        (l for l in STAGES if stage_conditions_for_location(l) and STAGE_ROUTES.get(l)), None)
    qclass = next(
        (c for c in CAR_CLASSES
         if vehicle_class_id_for_label(c) in CONFIRMED_VEHICLE_CLASS_IDS), None)
    qrxclass = next(
        (c for c in RX_CAR_CLASSES
         if vehicle_class_id_for_label(c) in CONFIRMED_VEHICLE_CLASS_IDS), None)

    # 19. championship builder (multi mode): new -> generate -> edit -> preview -> submit
    r = client.get(f'/clubs/{club_id}/championship/new')
    check('championship new page renders', r.status_code == 200, f'status={r.status_code}')

    r = client.post(f'/clubs/{club_id}/championship/new', data={
        'num_events': '1', 'num_stages': '1', 'mode': 'multi',
    }, follow_redirects=False)
    check('championship generate redirects to edit', r.status_code == 302 and '/championship/' in r.headers.get('Location', ''),
          f"loc={r.headers.get('Location')}")
    draft_id = r.headers.get('Location', '').rstrip('/').split('/')[-1]

    # Draft editor defaults: 7-day rally duration + a pre-selected conditions
    # pick (the per-location Daytime/Clear/Dry default, or its nearest twin).
    r = client.get(f'/clubs/{club_id}/championship/{draft_id}')
    conds_html = ''
    m = re.search(r'name="events\[0\]\[stages\]\[0\]\[conditions\]".*?</select>',
                  r.data.decode(), re.S)
    if m:
        sel = re.search(r'<option value="(\d+)"\s+selected', m.group(0))
        conds_html = m.group(0)
        conds_picked = bool(sel and sel.group(1))
    else:
        conds_picked = False
    check('edit page defaults: 7-day duration + pre-selected conditions',
          r.status_code == 200 and b'value="7" selected' in r.data and conds_picked,
          f'status={r.status_code} conds={conds_html[:120]!r}')

    # fill the single event's fields then preview
    routes = STAGE_ROUTES.get(qloc, []) if qloc else []
    edit_form = {
        'events[0][location]': qloc or '',
        'events[0][car_class]': qclass or '',
        'events[0][duration_days]': '1',
        'events[0][duration_hours]': '0',
        'events[0][duration_mins]': '0',
        'action': 'preview',
    }
    if routes:
        edit_form['events[0][stages][0][route]'] = str(routes[0][0])
        edit_form['events[0][stages][0][conditions]'] = str(stage_conditions_for_location(qloc)[0])
        edit_form['events[0][stages][0][surface_deg]'] = 'Medium'
        edit_form['events[0][stages][0][service_area]'] = 'Medium'

    r = client.post(f'/clubs/{club_id}/championship/{draft_id}', data=edit_form, follow_redirects=False)
    check('championship preview redirect', r.status_code == 302 and '/preview' in r.headers.get('Location', ''),
          f"loc={r.headers.get('Location')}")
    r = client.get(f'/clubs/{club_id}/championship/{draft_id}/preview')
    check('championship preview renders', r.status_code == 200, f'status={r.status_code}')
    check('preview advanced defaults: hardcore + cockpit on, unexpected + assists off',
          b'name="adv_hardcore_damage" value="1" checked' in r.data
          and b'name="adv_force_cockpit" value="1" checked' in r.data
          and b'name="adv_unexpected_moments" value="1" checked' not in r.data
          and b'name="adv_allow_assists" value="1" checked' not in r.data, f'status={r.status_code}')

    future_iso = time.strftime('%Y-%m-%dT%H:%M', time.gmtime(time.time() + 3600))
    r = client.post(f'/clubs/{club_id}/championship/{draft_id}/submit', data={
        'start_at': future_iso,
        'adv_hardcore_damage': '1',
        'adv_unexpected_moments': '1',
        'adv_allow_assists': '1',
    }, follow_redirects=False)
    check('championship submit redirects to club detail',
          r.status_code == 302 and r.headers.get('Location', '').rstrip('/') == f'/clubs/{club_id}',
          f"loc={r.headers.get('Location')}")
    with app.app_context():
        champ = (Event.query.filter_by(club_id=club_id)
                 .order_by(Event.start_time.asc()).first())
        check('championship stored as v2 event',
              champ is not None and champ.schema_version == 2 and len(json.loads(champ.legs_json or '[]')) >= 1, '')
        check('championship carries the club name',
              champ is not None and champ.name == 'Smoke Crew',
              f"name={champ.name if champ else None}")

    # 19b. rally season: per-event durations, staggered back-to-back windows
    r = client.post(f'/clubs/{club_id}/championship/new', data={
        'num_events': '2', 'num_stages': '1', 'mode': 'season',
    }, follow_redirects=False)
    check('season championship generate redirects to edit',
          r.status_code == 302 and '/championship/' in r.headers.get('Location', ''),
          f"loc={r.headers.get('Location')}")
    season_draft = r.headers.get('Location', '').rstrip('/').split('/')[-1]

    season_form = {'action': 'preview'}
    for i in range(2):
        season_form[f'events[{i}][location]'] = qloc or ''
        season_form[f'events[{i}][car_class]'] = qclass or ''
        season_form[f'events[{i}][duration_days]'] = '1' if i == 0 else '0'
        season_form[f'events[{i}][duration_hours]'] = '0' if i == 0 else '12'
        season_form[f'events[{i}][duration_mins]'] = '0'
        if routes:
            season_form[f'events[{i}][stages][0][route]'] = str(routes[0][0])
            season_form[f'events[{i}][stages][0][conditions]'] = str(stage_conditions_for_location(qloc)[0])
            season_form[f'events[{i}][stages][0][surface_deg]'] = 'Medium'
            season_form[f'events[{i}][stages][0][service_area]'] = 'Medium'

    r = client.post(f'/clubs/{club_id}/championship/{season_draft}', data=season_form, follow_redirects=False)
    check('season championship preview redirect',
          r.status_code == 302 and '/preview' in r.headers.get('Location', ''),
          f"loc={r.headers.get('Location')}")
    r = client.get(f'/clubs/{club_id}/championship/{season_draft}/preview')
    check('season preview renders with window column',
          r.status_code == 200 and b'Window (from start)' in r.data, f'status={r.status_code}')

    # Start after the multi championship's 1-day window so the one-per-club
    # conflict check passes.
    season_iso = time.strftime('%Y-%m-%dT%H:%M', time.gmtime(time.time() + 26 * 3600))
    r = client.post(f'/clubs/{club_id}/championship/{season_draft}/submit', data={
        'start_at': season_iso,
        'adv_hardcore_damage': '1',
        'adv_unexpected_moments': '1',
        'adv_allow_assists': '1',
    }, follow_redirects=False)
    check('season championship submit redirects to club detail',
          r.status_code == 302 and r.headers.get('Location', '').rstrip('/') == f'/clubs/{club_id}',
          f"loc={r.headers.get('Location')}")
    with app.app_context():
        season = (Event.query.filter_by(club_id=club_id)
                  .order_by(Event.start_time.desc()).first())
        check('season stored as v2 event',
              season is not None and season.schema_version == 2, '')
        if season is not None:
            legs = json.loads(season.legs_json or '[]')
            settings = json.loads(season.settings_json or '{}')
            zero = {'days': 0, 'hours': 0, 'mins': 0}
            check('season mode persisted in settings', settings.get('mode') == 'season',
                  f"settings={settings}")
            check('both season legs keep their own durations',
                  len(legs) == 2 and legs[0].get('duration') != zero
                  and legs[1].get('duration') != zero, f"legs={legs}")
            span = ((season.end_time - season.start_time).total_seconds()
                    if season.start_time and season.end_time else 0)
            check('season span equals sum of leg durations',
                  abs(span - (86400 + 12 * 3600)) < 60, f'span={span}')

    # 19c. rallycross lap option: per-event laps stored on the leg, shown in preview
    from app.tables import RX_LOCATIONS
    rxloc = next(iter(sorted(RX_LOCATIONS)), None)
    rx_conds = stage_conditions_for_location(rxloc) if rxloc else []
    rx_routes = STAGE_ROUTES.get(rxloc, []) if rxloc else []
    if rxloc and rx_conds and rx_routes:
        r = client.post(f'/clubs/{club_id}/championship/new', data={
            'num_events': '1', 'num_stages': '2', 'mode': 'multi',
        }, follow_redirects=False)
        rx_draft = r.headers.get('Location', '').rstrip('/').split('/')[-1]
        rx_form = {
            'events[0][location]': rxloc,
            'events[0][car_class]': qrxclass or '',
            'events[0][duration_days]': '1',
            'events[0][duration_hours]': '0',
            'events[0][duration_mins]': '0',
            'events[0][rx_laps]': '5',
            'action': 'preview',
        }
        for i in range(2):
            rx_form[f'events[0][stages][{i}][route]'] = str(rx_routes[i % len(rx_routes)][0])
            rx_form[f'events[0][stages][{i}][conditions]'] = str(rx_conds[0])
            rx_form[f'events[0][stages][{i}][surface_deg]'] = 'Medium'
            rx_form[f'events[0][stages][{i}][service_area]'] = 'Medium'
        r = client.post(f'/clubs/{club_id}/championship/{rx_draft}', data=rx_form, follow_redirects=False)
        check('rx championship preview redirect',
              r.status_code == 302 and '/preview' in r.headers.get('Location', ''),
              f"loc={r.headers.get('Location')}")
        r = client.get(f'/clubs/{club_id}/championship/{rx_draft}/preview')
        check('rx preview shows laps column with value',
              r.status_code == 200 and b'Laps' in r.data and b'>5<' in r.data,
              f'status={r.status_code}')
        r = client.get(f'/clubs/{club_id}/championship/{rx_draft}')
        check('event editor offers rally + rallycross classes on any track',
              r.status_code == 200
              and (qrxclass or '').encode() in r.data
              and (qclass or '').encode() in r.data,
              f'status={r.status_code}')
        # RX auto-pick: an RX location has exactly one verified route, so the
        # editor pre-selects it even when the stage carries no route yet.
        rx_tid = str(rx_routes[0][0])
        r = client.post(f'/clubs/{club_id}/championship/{rx_draft}', data={
            'events[0][location]': rxloc,
            'events[0][car_class]': qrxclass or '',
            'events[0][duration_days]': '1',
            'events[0][duration_hours]': '0',
            'events[0][duration_mins]': '0',
            'events[0][stages][0][route]': '',
            'events[0][stages][0][conditions]': str(rx_conds[0]),
            'events[0][stages][0][surface_deg]': 'Medium',
            'events[0][stages][0][service_area]': 'Medium',
            'action': 'save',
        }, follow_redirects=False)
        r = client.get(f'/clubs/{club_id}/championship/{rx_draft}')
        check('rx editor auto-picks the single route',
              r.status_code == 200 and f'value="{rx_tid}" selected'.encode() in r.data,
              f'status={r.status_code}')
        client.post(f'/clubs/{club_id}/championship/{rx_draft}', data=rx_form,
                    follow_redirects=False)
        rx_iso = time.strftime('%Y-%m-%dT%H:%M', time.gmtime(time.time() + 3 * 86400))
        r = client.post(f'/clubs/{club_id}/championship/{rx_draft}/submit', data={
            'start_at': rx_iso,
            'adv_hardcore_damage': '1',
            'adv_unexpected_moments': '1',
            'adv_allow_assists': '1',
        }, follow_redirects=False)
        check('rx championship submit redirects to club detail',
              r.status_code == 302 and r.headers.get('Location', '').rstrip('/') == f'/clubs/{club_id}',
              f"loc={r.headers.get('Location')}")
        with app.app_context():
            rx_event = (Event.query.filter_by(club_id=club_id)
                        .order_by(Event.start_time.desc()).first())
            rx_legs = json.loads(rx_event.legs_json or '[]') if rx_event else []
            check('rx laps stored on the leg',
                  len(rx_legs) == 1 and rx_legs[0].get('rx_laps') == 5,
                  f"legs={rx_legs}")
            check('rx event stores the rallycross car class',
                  (rx_event.car_class if rx_event else None) == qrxclass
                  and len(rx_legs) == 1 and rx_legs[0].get('car_class') == qrxclass,
                  f"event={rx_event.car_class if rx_event else None} legs={rx_legs}")

    # 19d. scoring system + power stage option: picked on the new screen, shown
    #      in preview, persisted in settings_json
    from app.scoring import codemasters_points
    check('codemasters formula P=8',
          [codemasters_points(8, i) for i in range(1, 9)] == [8, 5, 3, 2, 1, 1, 1, 1], '')
    check('codemasters formula P=4',
          [codemasters_points(4, i) for i in range(1, 5)] == [4, 2, 1, 1], '')
    from app.scoring import championship_points
    # Synthetic 2-rally championship (3 + 2 stages): A finishes everything, B
    # DNFs rally 1's last stage, C is fastest in rally 1 but DNFs rally 2's
    # final stage. WRC scale + power-stage bonus: A = 17+4 + 25+5 = 51,
    # B = 0 (DNF) + 17+4 = 21, C = 25+5 + 0 (DNF) = 30.
    fake_event = {
        'id': 'evt-fake-points', 'type': 'weekly',
        'events': [
            {'location': '', 'car_class': '', 'surface': 'Gravel',
             'duration': {'days': 1, 'hours': 0, 'mins': 0},
             'stages': [{'distance_km': 1.0}] * 3},
            {'location': '', 'car_class': '', 'surface': 'Gravel',
             'duration': {'days': 1, 'hours': 0, 'mins': 0},
             'stages': [{'distance_km': 1.0}] * 2},
        ],
        'settings': {'scoring': 'wrc', 'power_stage': True},
    }

    def _entry(name, times):
        return {'username': name, 'stages': [
            {'time_ms': t, 'penalties_ms': 0} for t in times]}

    fake_entries = [
        _entry('A', [100000, 100000, 100000, 100000, 100000]),
        _entry('B', [100000, 100000, 0, 100000, 110000]),
        _entry('C', [90000, 90000, 90000, 90000, 0]),
    ]
    check('wrc points: dnf takes position, power stage bonus on final stage',
          championship_points(fake_event, fake_entries) == {'A': 51, 'B': 21, 'C': 30},
          f"points={championship_points(fake_event, fake_entries)}")
    fake_event['settings'] = {'scoring': 'codemasters', 'power_stage': False}
    # Codemasters, P=3 per rally: p = [3, 2, 1]. Rally 1: C=3, A=2, B DNF=1;
    # rally 2: A=3, B=2, C DNF=1. Totals: A=5, B=3, C=4.
    check('codemasters points: field-size scale, dnf takes minimum 1 point',
          championship_points(fake_event, fake_entries) == {'A': 5, 'B': 3, 'C': 4},
          f"points={championship_points(fake_event, fake_entries)}")
    r = client.get(f'/clubs/{club_id}/championship/new')
    check('new screen offers scoring systems + power stage',
          r.status_code == 200 and b'name="scoring" value="wrc" checked' in r.data
          and b'name="scoring" value="groupb"' in r.data
          and b'name="scoring" value="codemasters"' in r.data
          and b'name="power_stage"' in r.data, f'status={r.status_code}')
    check('new screen defaults to rally season (listed first) + power stage card',
          b'name="mode" value="season" checked' in r.data
          and r.data.index(b'value="season"') < r.data.index(b'value="multi"')
          and b'The original scoring system, based on amount of drivers in the club' in r.data
          and b'>Power stage points<' in r.data, f'status={r.status_code}')
    check('new screen: numbered event/stage pickers (12 tiles each, 3/5 preselected)',
          r.data.count(b'name="num_events" value=') == 12
          and b'name="num_events" value="3" checked' in r.data
          and r.data.count(b'name="num_stages" value=') == 12
          and b'name="num_stages" value="5" checked' in r.data, f'status={r.status_code}')
    r = client.post(f'/clubs/{club_id}/championship/new', data={
        'num_events': '1', 'num_stages': '1', 'mode': 'multi',
        'scoring': 'codemasters', 'power_stage': '1',
    }, follow_redirects=False)
    check('scoring championship generate redirects to edit',
          r.status_code == 302 and '/championship/' in r.headers.get('Location', ''),
          f"loc={r.headers.get('Location')}")
    sc_draft = r.headers.get('Location', '').rstrip('/').split('/')[-1]
    sc_form = {
        'events[0][location]': qloc or '',
        'events[0][car_class]': qclass or '',
        'events[0][duration_days]': '1',
        'events[0][duration_hours]': '0',
        'events[0][duration_mins]': '0',
        'action': 'preview',
    }
    if routes:
        sc_form['events[0][stages][0][route]'] = str(routes[0][0])
        sc_form['events[0][stages][0][conditions]'] = str(stage_conditions_for_location(qloc)[0])
        sc_form['events[0][stages][0][surface_deg]'] = 'Medium'
        sc_form['events[0][stages][0][service_area]'] = 'Medium'
    r = client.post(f'/clubs/{club_id}/championship/{sc_draft}', data=sc_form, follow_redirects=False)
    check('scoring championship preview redirect',
          r.status_code == 302 and '/preview' in r.headers.get('Location', ''),
          f"loc={r.headers.get('Location')}")
    r = client.get(f'/clubs/{club_id}/championship/{sc_draft}/preview')
    check('preview shows codemasters + power stage enabled',
          r.status_code == 200 and b'Codemasters' in r.data
          and b'power stage points enabled' in r.data, f'status={r.status_code}')
    sc_iso = time.strftime('%Y-%m-%dT%H:%M', time.gmtime(time.time() + 5 * 86400))
    r = client.post(f'/clubs/{club_id}/championship/{sc_draft}/submit', data={
        'start_at': sc_iso,
        'adv_hardcore_damage': '1',
        'adv_unexpected_moments': '1',
        'adv_allow_assists': '1',
    }, follow_redirects=False)
    check('scoring championship submit redirects to club detail',
          r.status_code == 302 and r.headers.get('Location', '').rstrip('/') == f'/clubs/{club_id}',
          f"loc={r.headers.get('Location')}")
    with app.app_context():
        sc_event = (Event.query.filter_by(club_id=club_id)
                   .order_by(Event.start_time.desc()).first())
        sc_settings = json.loads(sc_event.settings_json or '{}') if sc_event else {}
        check('scoring system + power stage persisted in settings',
              sc_settings.get('scoring') == 'codemasters'
              and sc_settings.get('power_stage') is True,
              f"settings={sc_settings}")

    # 19f. club/event detail pages: rounds table, standings with points,
    #      per-round classification and stage-by-stage results
    with app.app_context():
        from app.models import Result as _Result
        champ = (Event.query.filter_by(club_id=club_id)
                 .order_by(Event.start_time.asc()).first())
        eid2 = champ.id if champ else None
        legs2 = json.loads(champ.legs_json or '[]') if champ else []
        nstages = sum(len(l.get('stages', []) or []) for l in legs2)
        # smokedriver is faster (150s/stage) than secondpilot (200s/stage)
        for name, t in (('smokedriver', 150000), ('secondpilot', 200000)):
            db.session.add(_Result(
                event_id=eid2, username=name, car='Test Car',
                total_time_ms=t * nstages,
                stages_json=json.dumps([{'time_ms': t, 'penalties_ms': 0}] * nstages),
            ))
        db.session.commit()
    if eid2 and nstages:
        r = client.get(f'/clubs/{club_id}')
        check('club detail shows rounds + standings with times',
              r.status_code == 200 and b'Standings' in r.data
              and (qloc or '').encode() in r.data
              and b'2:30.000' in r.data and b'3:20.000' in r.data
              and b'sweden-flag.png' in r.data,
              f'status={r.status_code}')
        r = client.get(f'/event/{eid2}')
        check('event detail shows rounds + classification + stage results',
              r.status_code == 200 and b'Round 1' in r.data
              and b'Classification' in r.data
              and b'Stage 1' in r.data
              and b'sweden-flag.png' in r.data
              and r.data.index(b'smokedriver') < r.data.index(b'secondpilot'),
              f'status={r.status_code}')

    # 19e. Car classes: any confirmed class is valid on any track type
    #      (rally + RX classes both selectable everywhere).  Multi-Location
    #      Rally: one class across all events (car state carries over).
    #      Rally Season: each rally may use its own class.
    from app.tables import validate_championship, CHAMP_MODE_SEASON
    check('all rx class labels resolve to confirmed ids',
          all(vehicle_class_id_for_label(c) in CONFIRMED_VEHICLE_CLASS_IDS
              for c in RX_CAR_CLASSES), f"classes={list(RX_CAR_CLASSES)}")

    def _cev(loc, cls):
        conds = stage_conditions_for_location(loc)
        rts = STAGE_ROUTES.get(loc, [])
        return {'location': loc, 'car_class': cls,
                'duration': {'days': 1, 'hours': 0, 'mins': 0},
                'rx_laps': None,
                'stages': [{'track_id': rts[0][0] if rts else None,
                            'conditions_id': conds[0] if conds else None,
                            'surface_deg': 'Medium', 'service_area': 'Medium'}]}

    def _class_errors(evs, mode=None):
        if mode is None:
            return [e for e in validate_championship(evs) if 'vehicle class' in e]
        return [e for e in validate_championship(evs, mode=mode) if 'vehicle class' in e]

    errs = _class_errors([_cev(rxloc, qrxclass)])
    check('rx event + rx class passes validation', not errs, str(errs))
    errs = _class_errors([_cev(rxloc, qclass)])
    check('rx event + rally class allowed', not errs, str(errs))
    errs = _class_errors([_cev(qloc, qrxclass)])
    check('rally event + rx class allowed', not errs, str(errs))
    # Multi-Location Rally: one class across all events (shared car state)
    errs = _class_errors([_cev(qloc, qclass), _cev(rxloc, qrxclass)], 'multi')
    check('multi rally: different classes rejected',
          any('same vehicle class' in e for e in errs), str(errs))
    other_rx = next((c for c in RX_CAR_CLASSES if c != qrxclass), None)
    if other_rx:
        errs = _class_errors([_cev(rxloc, qrxclass), _cev(rxloc, other_rx)], 'multi')
        check('multi rally: two rx events with different classes rejected',
              any('same vehicle class' in e for e in errs), str(errs))
    other_rally = next((c for c in CAR_CLASSES if c != qclass), None)
    if other_rally:
        errs = _class_errors([_cev(qloc, qclass), _cev(qloc, other_rally)], 'multi')
        check('multi rally: two rally events with different classes rejected',
              any('same vehicle class' in e for e in errs), str(errs))
    # Rally Season: each rally may use its own class
    if other_rx:
        check('season: mixed disciplines with different classes allowed',
              not _class_errors([_cev(qloc, qclass), _cev(rxloc, other_rx)], CHAMP_MODE_SEASON),
              str(_class_errors([_cev(qloc, qclass), _cev(rxloc, other_rx)], CHAMP_MODE_SEASON)))
    if other_rally:
        check('season: two rally events with different classes allowed',
              not _class_errors([_cev(qloc, qclass), _cev(qloc, other_rally)], CHAMP_MODE_SEASON),
              str(_class_errors([_cev(qloc, qclass), _cev(qloc, other_rally)], CHAMP_MODE_SEASON)))

    # 20. delete the championships
    with app.app_context():
        champ_ids = [e.id for e in Event.query.filter_by(club_id=club_id).all()]
    for eid in champ_ids:
        r = client.get(f'/clubs/{club_id}/championship/{eid}/delete')
        check(f'championship delete confirm renders ({eid})', r.status_code == 200, f'status={r.status_code}')
        r = client.post(f'/clubs/{club_id}/championship/{eid}/delete', follow_redirects=False)
        with app.app_context():
            check(f'championship deleted ({eid})', db.session.get(Event, eid) is None, '')

    # 21. discard a fresh draft
    r = client.post(f'/clubs/{club_id}/championship/new', data={
        'num_events': '1', 'num_stages': '1',
    }, follow_redirects=False)
    disc_draft = r.headers.get('Location', '').rstrip('/').split('/')[-1]
    r = client.post(f'/clubs/{club_id}/championship/{disc_draft}/discard', follow_redirects=False)
    check('draft discard redirects to club detail',
          r.status_code == 302 and r.headers.get('Location', '').rstrip('/') == f'/clubs/{club_id}',
          f"status={r.status_code} loc={r.headers.get('Location')}")

    # 21b. club list ordering: soonest-expiring championship first
    for cname in ('Order Alpha', 'Order Beta', 'Order Season'):
        r = client.post('/clubs', data={
            'name': cname, 'description': '',
            'visibility': 'public', 'join_policy': 'open',
        }, follow_redirects=False)
        check(f'ordering fixture club created ({cname})', r.status_code == 302, f'status={r.status_code}')
    with app.app_context():
        from datetime import timedelta as _td
        from app.models import utcnow as _utcnow
        now = _utcnow()
        alpha = Club.query.filter_by(name='Order Alpha').first()
        beta = Club.query.filter_by(name='Order Beta').first()
        season = Club.query.filter_by(name='Order Season').first()
        db.session.add(Event(id='evt-smoke-alpha', name='Order Alpha', club_id=alpha.id,
                             start_time=now - _td(hours=1), end_time=now + _td(days=5),
                             active=True))
        db.session.add(Event(id='evt-smoke-beta', name='Order Beta', club_id=beta.id,
                             start_time=now - _td(hours=1), end_time=now + _td(hours=2),
                             active=True))
        # Rally Season: three back-to-back 4-day legs. The stored end_time is
        # the LAST leg's end (+11d23h) but the current leg ends in +3d23h, so
        # ordering must use the current leg — it sorts before Alpha (+5d).
        season_legs = [
            {'name': f'Round {i + 1}', 'location': f'Loc {i + 1}',
             'car_class': 'class_a',
             'stages': [{'id': f's{i}a', 'name': f'Stage A {i + 1}'},
                        {'id': f's{i}b', 'name': f'Stage B {i + 1}'}],
             'duration': {'days': 4, 'hours': 0, 'mins': 0}}
            for i in range(3)
        ]
        db.session.add(Event(id='evt-smoke-season', name='Order Season', club_id=season.id,
                             start_time=now - _td(hours=1), end_time=now + _td(days=12, hours=-1),
                             settings_json=json.dumps({'mode': 'season'}),
                             legs_json=json.dumps(season_legs), active=True))
        db.session.commit()
        order_club_ids = [alpha.id, beta.id, season.id]
    r = client.get('/clubs')
    html = r.get_data(as_text=True)
    check('club list renders with ordering fixtures', r.status_code == 200, f'status={r.status_code}')
    import re as _re
    cards = _re.findall(r'title-link" href="/clubs/[^"]+">([^<]+)<', html)
    check('soonest-expiring club listed first (season by current leg)',
          all(n in cards for n in ('Order Alpha', 'Order Beta', 'Order Season'))
          and cards.index('Order Beta') < cards.index('Order Season') < cards.index('Order Alpha'),
          str(cards))
    check('live chip shows countdown on club card', 'Live — ends in' in html)
    s_pos = html.find('>Order Season<')
    season_card = html[s_pos:s_pos + 600] if s_pos >= 0 else ''
    # Render happens seconds after the fixture `now`, so +3d23h shows as "3d 22h".
    check('season card counts down to current leg end, not last leg end',
          '3d 2' in season_card and '11d' not in season_card, season_card[:400])

    # 21c. in-game club list uses the same ordering (bug #15)
    r = client.get('/api/game/clubs', headers=headers)
    j = r.get_json() or {}
    game_names = [c.get('name') for c in j.get('clubs', [])]
    check('game clubs ordered by soonest-ending championship (season by current leg)',
          j.get('ok') is True
          and all(n in game_names for n in ('Order Alpha', 'Order Beta', 'Order Season'))
          and game_names.index('Order Beta') < game_names.index('Order Season')
          < game_names.index('Order Alpha'), str(game_names))

    # 21d. dashboard: live events show championship mode, clubs link out (#46)
    r = client.get('/dashboard')
    html = r.get_data(as_text=True)
    check('dashboard renders', r.status_code == 200, f'status={r.status_code}')
    check('dashboard live-events Type column shows championship mode',
          'Rally Season' in html and 'Multi-Location Rally' in html
          and '<span class="chip">club</span>' not in html)
    check('dashboard my-clubs names link to club detail pages',
          all(f'href="/clubs/{cid}"' in html for cid in order_club_ids))

    # 23. home page: site stats + footer social links (backlog #25-#29)
    r = client.get('/')
    html = r.get_data(as_text=True)
    check('home renders', r.status_code == 200, f'status={r.status_code}')
    check('footer links: github/ko-fi/twitch/discord',
          'github.com/Agersi/DR3' in html and 'ko-fi.com/agersi' in html
          and 'twitch.tv/agersi' in html
          and 'discord.com/invite/U3h9dWGgGY' in html
          and 'Join the community' in html)
    check('footer shows open-source line + donate',
          'open source' in html and 'Donate' in html)
    check('footer shows trademark notice',
          'trademark of Codemasters' in html
          and 'not affiliated with Codemasters or EA' in html)
    with app.app_context():
        from app.models import Result as _Result2
        n_drivers = User.query.count()
        n_clubs = Club.query.count()
        n_stages = sum(1 for rr in _Result2.query.all()
                       for s in json.loads(rr.stages_json or '[]')
                       if int(s.get('time_ms', 0) or 0) > 0)
    nums = {label: num for num, label in _re.findall(
        r'stat-num">(\d+)</span><span class="stat-label">([^<]+)<', html)}
    check('home stat cards match DB counts',
          nums.get('Drivers') == str(n_drivers) and nums.get('Clubs') == str(n_clubs)
          and nums.get('Stages completed') == str(n_stages), str(nums))
    check('home lists recent stage time (smokedriver 6:59.000)',
          'Smoke Driver' in html and '6:59.000' in html)

    # 24. club delete: confirm page, owner-only, destructive cascade
    r = client.post('/clubs', data={
        'name': 'Doomed Club', 'description': '',
        'visibility': 'public', 'join_policy': 'open',
    }, follow_redirects=False)
    check('delete fixture club created', r.status_code == 302, f'status={r.status_code}')
    doomed_id = r.headers.get('Location', '').rstrip('/').split('/')[-1]
    with app.app_context():
        from datetime import timedelta as _td3
        from app.models import Result as _ResD, utcnow as _utcnow_d
        nowd = _utcnow_d()
        db.session.add(Event(id='evt-smoke-doomed', name='Doomed Champ', club_id=doomed_id,
                             start_time=nowd - _td3(hours=1), end_time=nowd + _td3(days=2),
                             active=True))
        db.session.add(_ResD(event_id='evt-smoke-doomed', username='smokedriver',
                             car='Test Car', total_time_ms=90000,
                             stages_json=json.dumps([{'time_ms': 45000, 'penalties_ms': 0}])))
        db.session.commit()
    r = client.get(f'/clubs/{doomed_id}/delete')
    check('club delete confirm renders',
          r.status_code == 200 and b'Delete Club' in r.data and b'Doomed Club' in r.data,
          f'status={r.status_code}')
    client2.post('/login', data={'username': 'secondpilot', 'password': 'password123'},
                 follow_redirects=False)
    r = client2.get(f'/clubs/{doomed_id}/delete')
    check('non-owner club delete rejected', r.status_code == 403, f'status={r.status_code}')
    r = client.post(f'/clubs/{doomed_id}/delete', follow_redirects=False)
    check('club delete redirects to club list',
          r.status_code == 302 and r.headers.get('Location', '').rstrip('/') == '/clubs',
          f"loc={r.headers.get('Location')}")
    with app.app_context():
        check('club row deleted', db.session.get(Club, doomed_id) is None, '')
        check('club events deleted', Event.query.filter_by(club_id=doomed_id).count() == 0, '')
        check('club results deleted', _ResD.query.filter_by(event_id='evt-smoke-doomed').count() == 0, '')
        check('club members removed', ClubMember.query.filter_by(club_id=doomed_id).count() == 0, '')
    # The name still appears in the success flash, so assert on the card link.
    r = client.get('/clubs')
    check('deleted club gone from list', f'/clubs/{doomed_id}' not in r.get_data(as_text=True))

    # 25. account delete: confirm page, owner-block, full cascade
    client5 = app.test_client()
    client5.post('/register', data={
        'username': 'doomedpilot', 'email': 'doomed@example.com',
        'password': 'password123', 'confirm': 'password123',
        'display_name': 'Doomed Pilot', 'country': 'Norway',
    })
    with app.app_context():
        u5 = User.query.filter_by(username='doomedpilot').first()
        v5 = u5.verify_token if u5 else ''
        pilot_uid = u5.id if u5 else 0
    client5.get(f'/verify_email/{v5}', follow_redirects=True)

    r = client5.post('/clubs', data={
        'name': 'Pilot Club', 'description': '',
        'visibility': 'public', 'join_policy': 'open',
    }, follow_redirects=False)
    check('account-delete fixture club created', r.status_code == 302, f'status={r.status_code}')
    pilot_id = r.headers.get('Location', '').rstrip('/').split('/')[-1]

    with app.app_context():
        from datetime import timedelta as _td5
        from app.models import ChampionshipDraft as _Draft, Result as _Res5, utcnow as _utcnow5
        now5 = _utcnow5()
        db.session.add(Event(id='evt-smoke-pilot', name='Pilot Champ', club_id=club_id,
                             start_time=now5 - _td5(hours=1), end_time=now5 + _td5(days=2),
                             active=True))
        db.session.add(_Res5(event_id='evt-smoke-pilot', username='doomedpilot',
                             car='Test Car', total_time_ms=60000,
                             stages_json=json.dumps([{'time_ms': 30000, 'penalties_ms': 0}])))
        db.session.add(_Draft(id='draft-smoke-pilot', club_id=club_id, owner='doomedpilot',
                              data_json='{}'))
        db.session.commit()
    r = client5.post(f'/clubs/{club_id}/join', follow_redirects=False)
    check('account-delete fixture joined main club', r.status_code == 302, f'status={r.status_code}')

    r = client5.get('/account/delete')
    html = r.get_data(as_text=True)
    check('account delete confirm renders',
          r.status_code == 200 and 'Delete Account' in html and 'Pilot Club' in html,
          f'status={r.status_code}')

    r = client5.post('/account/delete', follow_redirects=False)
    check('delete blocked while owning a club',
          r.status_code == 302 and '/account/delete' in r.headers.get('Location', ''),
          f"loc={r.headers.get('Location')}")
    with app.app_context():
        check('user still exists after blocked delete',
              User.query.filter_by(username='doomedpilot').first() is not None, '')

    r = client5.post(f'/clubs/{pilot_id}/delete', follow_redirects=False)
    check('owned club deleted first', r.status_code == 302, f'status={r.status_code}')

    r = client5.post('/account/delete', follow_redirects=False)
    check('account delete redirects to login',
          r.status_code == 302 and '/login' in r.headers.get('Location', ''),
          f"loc={r.headers.get('Location')}")
    with app.app_context():
        check('user row deleted', User.query.filter_by(username='doomedpilot').first() is None, '')
        check('memberships removed', ClubMember.query.filter_by(user_id=pilot_uid).count() == 0, '')
        check('results deleted', _Res5.query.filter_by(username='doomedpilot').count() == 0, '')
        check('drafts deleted', _Draft.query.filter_by(owner='doomedpilot').count() == 0, '')
        check('owned club gone', db.session.get(Club, pilot_id) is None, '')

    # 22. subpath deployment (production serves under /rally; nginx strips the
    #     prefix so routes stay root-level, but url_for must emit prefixed URLs)
    from app import _apply_subpath
    app2 = create_app()
    app2.config['SITE_URL'] = 'https://byagersi.com/rally'
    _apply_subpath(app2)
    c2 = app2.test_client()
    r = c2.get('/login')
    html = r.get_data(as_text=True)
    check('subpath: root-level routes still resolve', r.status_code == 200, f'status={r.status_code}')
    check('subpath: links are /rally-prefixed', '/rally/login' in html and 'href="/login"' not in html)
    r = c2.post('/login', data={'username': 'smokedriver', 'password': 'wrongpass'},
                follow_redirects=False)
    check('subpath: redirects are /rally-prefixed',
          r.headers.get('Location', '').startswith('/rally/'), f"loc={r.headers.get('Location')}")

    print()
    if failures:
        print(f'{len(failures)} FAILURES: {failures}')
        return 1
    print('ALL SMOKE TESTS PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
