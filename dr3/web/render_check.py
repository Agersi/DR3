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

    # clubs list: filter form + partial reload (only the results section swaps)
    html = client.get('/clubs').get_data(as_text=True)
    check('clubs: filter form + partial-reload script',
          'name="drivetrain"' in html and 'name="mode"' in html
          and 'id="club-results"' in html
          and "getElementById('club-results')" in html
          and "addEventListener('input'" in html and 'clearTimeout' in html
          and '>Search</button>' not in html, '')
    check('clubs: filter panel below create-club + sticky',
          'class="panel filters"' in html
          and client.get('/static/css/style.css').get_data(as_text=True)
          .find('.filters { position: sticky; top: 0') != -1, '')
    css = client.get('/static/css/style.css').get_data(as_text=True)
    check('clubs: filter tile styling scoped to .filters (builder page untouched)',
          '.filters { position: sticky; top: 0; z-index: 20; padding: 0.9rem; }' in css
          and '.filters .opt-grid { grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); margin: 0.6rem 0 0 0; }' in css
          and '.filters .opt-title { text-align: center; letter-spacing: 0.01em; }' in css
          and '.opt-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));' in css
          and 'letter-spacing: 0.07em' in css, '')
    check('clubs: opt-style filter tiles (active toggle + cycling states)',
          'class="opt fcycle"' in html and 'data-states' in html
          and 'opt-state' in html and 'name="active"' in html
          and '<select name=' not in html, '')
    check('clubs: active-tile label flips with state',
          'Show only active clubs' in html
          and 'Show all clubs' in client.get('/clubs?active=1').get_data(as_text=True)
          and "box.checked ? 'Show all clubs'" in html, '')

    # home recent-times box (backlog #42): server-rendered first page +
    # infinite-scroll JS wired to the public endpoint
    html = client.get('/').get_data(as_text=True)
    check('home: recent-times box with rows + scroll JS',
          'recent-times-box' in html and 'recent-times-body' in html
          and 'Smoke Driver' in html
          and 'data-url="/api/home/recent-times"' in html
          and "addEventListener('scroll'" in html, '')

    # home hero: three panels for anonymous visitors (Join Now / What's New / Discord)
    check('home anon: Join Now panel with Register + How it works',
          '<h3>Join Now</h3>' in html
          and 'Create an account to start competing in the DR3 clubs!' in html
          and 'Register</a>' in html
          and 'How it works' in html, '')
    check('home anon: What\'s New panel lists DR3 additions',
          "<h3>What's New</h3>" in html
          and 'RallyCross cars and tracks' in html
          and 'Multi-Location' in html
          and 'Better stats' in html, '')
    check('home anon: Join Now notes unaffected original features',
          'The Career System, Dailies, Weeklies, Monthlies, Time Trials and Multiplayer Lobbies are unaffected'
          in html
          and 'continue to work as usual' in html
          and html.index('<h3>Join Now</h3>') < html.index('The Career System,'), '')
    check('home anon: Discord panel lists what to join for',
          '<li>Suggest features</li>' in html
          and '<li>Report bugs and errors</li>' in html
          and '<li>Chat with other Rally Enthusiasts!</li>' in html, '')
    check('home anon: Discord panel links to the invite',
          '<h3>Discord</h3>' in html and 'Join the Discord' in html
          and 'discord.com/invite/U3h9dWGgGY' in html, '')
    check('home anon: What\'s New + Discord intro lines removed',
          'What DR3 adds on top of the original club system' not in html
          and 'Join the Discord to:' not in html, '')
    check('home anon: old single hero removed', 'class="hero"' not in html, '')
    css = client.get('/static/css/style.css').get_data(as_text=True)
    check('home panels CSS: wider middle + equal-height + smaller list text',
          'grid-template-columns: 1.2fr 1.5fr 1fr' in css
          and 'align-items: stretch' in css
          and 'padding-left: 1.25rem; font-size: .9rem' in css
          and '.home-panels .discord-list { font-size: 1rem; }' in css, '')
    check('home: support section Twitch line',
          "Follow me on Twitch, it's a free way to show your support for the project" in html, '')

    zip_path = HERE.parent / 'dist' / 'DR3-windows.zip'
    want_dl = 200 if zip_path.is_file() else 404
    r = client.get('/download/DR3-windows.zip')
    check('anon GET /download/DR3-windows.zip', r.status_code == want_dl, f'status={r.status_code}')

    # install page: auto-launch panel with the launcher recipe
    html = client.get('/install').get_data(as_text=True)
    check('install: auto-launch panel + launcher bat + Steam launch options',
          'id="auto-launch"' in html
          and 'Start automatically when DR3 opens' in html
          and 'start "" "C:\\DR3\\DR3.exe"' in html
          and '%*' in html
          and '"C:\\Users\\YOURNAME\\Desktop\\DR3.bat" %command%' in html
          and 'does not expand Windows environment variables' in html, '')

    r = client.post('/login', data={'username': 'smokedriver', 'password': 'password123'})
    check('login', r.status_code in (302, 200), f'status={r.status_code}')

    # home hero: first panel becomes "Setup" with a link to the account page
    auth_home = client.get('/').get_data(as_text=True)
    check('home auth: Setup panel links to the account page',
          '<h3>Setup</h3>' in auth_home and 'Open account' in auth_home
          and '/account"' in auth_home, '')
    check('home auth: Join Now panel replaced by Setup',
          '<h3>Join Now</h3>' not in auth_home, '')
    check('home auth: What\'s New + Discord panels unchanged',
          "<h3>What's New</h3>" in auth_home and '<h3>Discord</h3>' in auth_home, '')

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
