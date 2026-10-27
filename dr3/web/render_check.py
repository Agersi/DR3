"""Render check: GET every page and assert expected status codes.

Run AFTER smoke_test.py (it reuses smoke.db and the smokedriver account it creates).
    .venv\\Scripts\\python.exe smoke_test.py
    .venv\\Scripts\\python.exe render_check.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if not (HERE / 'smoke.db').exists():
    print('smoke.db not found — run smoke_test.py first.')
    raise SystemExit(1)
os.environ['SECRET_KEY'] = 'smoke-test-key'
os.environ['DATABASE_URL'] = f'sqlite:///{HERE / "smoke.db"}'
os.environ['CRON_API_KEY'] = 'smoke-cron-key'
os.environ['DR3_DISABLE_CSRF'] = '1'
os.environ.pop('SESSION_COOKIE_SECURE', None)

sys.path.insert(0, str(HERE))

from app import create_app  # noqa: E402
from app.db import db  # noqa: E402
from app.models import Club, Event  # noqa: E402


def main() -> int:
    app = create_app()
    client = app.test_client()
    failures = []

    def check(name, cond, extra=''):
        print(f'[{"ok" if cond else "FAIL"}] {name}' + (f' — {extra}' if extra and not cond else ''))
        if not cond:
            failures.append(name)

    anon_pages = [('/', 200), ('/events', 404), ('/clubs', 200), ('/install', 200),
                  ('/about', 200), ('/login', 200), ('/register', 200),
                  ('/forgot_password', 200), ('/static/css/style.css', 200),
                  ('/static/logo.png', 200), ('/nope', 404)]
    for path, want in anon_pages:
        r = client.get(path)
        check(f'anon GET {path}', r.status_code == want, f'status={r.status_code}')

    # home recent-times box (backlog #42): server-rendered first page +
    # infinite-scroll JS wired to the public endpoint
    html = client.get('/').get_data(as_text=True)
    check('home: recent-times box with rows + scroll JS',
          'recent-times-box' in html and 'recent-times-body' in html
          and 'Smoke Driver' in html
          and 'data-url="/api/home/recent-times"' in html
          and "addEventListener('scroll'" in html, '')

    zip_path = HERE.parent / 'dist' / 'DR3-windows.zip'
    want_dl = 200 if zip_path.is_file() else 404
    r = client.get('/download/DR3-windows.zip')
    check('anon GET /download/DR3-windows.zip', r.status_code == want_dl, f'status={r.status_code}')

    r = client.post('/login', data={'username': 'smokedriver', 'password': 'password123'})
    check('login', r.status_code in (302, 200), f'status={r.status_code}')

    auth_pages = ['/dashboard', '/account', '/notifications', '/profile/smokedriver']
    for path in auth_pages:
        r = client.get(path)
        check(f'auth GET {path}', r.status_code == 200, f'status={r.status_code}')

    with app.app_context():
        club = Club.query.first()
        events = Event.query.all()
        club_id = club.id if club else None
        event_ids = [e.id for e in events]

    if club_id:
        r = client.get(f'/clubs/{club_id}')
        check(f'auth GET /clubs/{club_id}', r.status_code == 200, f'status={r.status_code}')
        # championship builder: create a fresh draft, render edit + preview
        r = client.post(f'/clubs/{club_id}/championship/new', data={'num_events': '2', 'num_stages': '3'}, follow_redirects=False)
        check('champ new -> redirect to edit', r.status_code == 302, f'status={r.status_code}')
        if r.status_code == 302:
            edit_path = r.headers['Location']
            if edit_path.startswith('/'):
                edit_path = edit_path.split('?')[0]
            r = client.get(edit_path)
            check(f'auth GET {edit_path}', r.status_code == 200, f'status={r.status_code}')
            draft_id = edit_path.rstrip('/').split('/')[-1]
            r = client.get(f'/clubs/{club_id}/championship/{draft_id}/preview')
            check('auth GET championship preview', r.status_code == 200, f'status={r.status_code}')
            r = client.post(f'/clubs/{club_id}/championship/{draft_id}/discard')
            check('champ draft discarded', r.status_code in (302, 200), f'status={r.status_code}')

    for eid in event_ids:
        r = client.get(f'/event/{eid}')
        check(f'auth GET /event/{eid}', r.status_code == 200, f'status={r.status_code}')

    print()
    if failures:
        print(f'RENDER CHECK FAILED: {len(failures)} failure(s)')
        return 1
    print('ALL RENDER CHECKS PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
