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
              and           '.filters .opt-title { text-align: center; letter-spacing: 0.01em; font-size: 0.74rem; }' in css
          and           '.filters .opt { display: flex; flex-direction: column; transition: transform 0.12s ease, box-shadow 0.12s ease; }' in css
          and '.filters .opt:hover {' in css and 'transform: scale(1.03)' in css
          and 'inset 0 0 18px -8px rgba(175, 175, 175, 0.6)' in css
              and '.opt-state { display: block; margin-top: auto; text-align: center; text-transform: uppercase; color: var(--text); font-size: 0.74rem; line-height: 1.45; font-weight: 900; font-style: italic; }' in css
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
    check('clubs: card chips row — space-between, stretched members chip, full-width live chip',
          'class="chip chip-warn members-chip"' in html
          and '.chips-row { display: flex; gap: 0.45rem; flex-wrap: wrap; align-items: center; margin: 0.3rem 0 0.8rem; }' in css
          and '.club-card .chips-row { justify-content: space-between; }' in css
          and '.club-card .chips-row .members-chip { flex: 1; text-align: center; padding: 0.28rem 0.7rem; font-size: 0.78rem; }' in css
          and '.club-card .chips-row .chip-ok { width: 100%; text-align: center; }' in css, '')
    tpl = (HERE / 'templates' / 'clubs.html').read_text(encoding='utf-8')
    check('clubs: upcoming chip uses chip-ok + owner chip between title and desc',
          'class="chip chip-ok">Starts in' in tpl
          and 'chip chip-accent">Starts in' not in tpl
          and 'class="chip chip-warn owner-chip">Owner: {{ c.owner.username }}' in tpl
          and tpl.find('owner-chip') > tpl.find('title-link')
          and tpl.find('owner-chip') < tpl.find('class="desc"'), '')
    check('clubs: owner chip rendered full-width centered + title centered one line',
          'class="chip chip-warn owner-chip">Owner: ' in html
          and '.club-card .owner-chip {\n  align-self: flex-start;\n  width: 100%;\n  text-align: center;\n}' in css
          and '.club-card .title-link {\n  display: block;\n  width: 100%;\n  text-align: center;\n  white-space: nowrap;\n  overflow: hidden;\n  text-overflow: ellipsis;\n  color: var(--text);\n  text-decoration: none;\n  font-size: 1.15rem;\n  letter-spacing: 0.01em;\n}' in css, '')
    check('site-wide: bordered boxes get 0.3rem border radius',
          'border-radius: 0.3rem;' in css
          and '.panel, .club-card, .stat-card, .btn, button, input, select, textarea,' in css
          and '.opt, .num-tile, .table-wrap, .chip, .flash' in css
          and '.tyre-chip, .champ-center, .round-thumb, .round-class-thumb { border-radius: 0.3rem; }' in css, '')

    # home recent-times box (backlog #42): server-rendered first page +
    # infinite-scroll JS wired to the public endpoint
    html = client.get('/').get_data(as_text=True)
    check('home: recent-times box with rows + scroll JS',
          'recent-times-box' in html and 'recent-times-body' in html
          and 'Smoke Driver' in html
          and 'data-url="/api/home/recent-times"' in html
          and "addEventListener('scroll'" in html, '')
    check('home: recent-times rows link driver + car pages',
          '<a href="/profile/smokedriver">Smoke Driver</a>' in html
          and '<a href="/cars/1">Vehicle 1</a>' in html, '')
    r = client.get('/cars/565')
    check('car stats page renders name + class',
          r.status_code == 200 and 'Porsche 911 RGT Rally Spec' in r.get_data(as_text=True)
          and 'Rally GT' in r.get_data(as_text=True), f'status={r.status_code}')
    check('recent-box uses a custom scrollbar matching the palette',
          '.recent-box { max-height: 360px; overflow-y: auto; scrollbar-width: thin; scrollbar-color: var(--line) rgba(24, 28, 20, 0.5); }' in css
          and '.recent-box::-webkit-scrollbar { width: 10px; }' in css
          and '.recent-box::-webkit-scrollbar-thumb { background: var(--line); border: 2px solid var(--card); }' in css, '')
    check('home: stat cards link out (drivers + combined clubs card) + terminal-damage card',
          '<a class="stat-card stat-drivers" href="/drivers">' in html
          and '<a class="stat-link" href="/clubs">' in html
          and '<a class="stat-link" href="/clubs?active=1">' in html
          and 'Kilometers Driven' in html
          and 'Miles Driven' in html
          and 'Stages Driven' in html
          and 'Survival Rate' in html
          and 'Days without a Terminal Damage' in html
          and 'stat-sub' in html
          and client.get('/static/css/style.css').get_data(as_text=True)
          .find('a.stat-card:hover {') != -1, '')
    check('home: per-card stat-num font sizes (drivers 5rem, days-without-TD 3.5rem)',
          '.stat-drivers .stat-num { font-size: 5rem; }' in css
          and '.stat-days .stat-num { font-size: 3.5rem; }' in css
          and '<div class="stat-card stat-days">' in html, '')
    check('home: stat card base CSS (space-between + centered)',
          '.stat-card {\n  background: var(--card);\n  border: 2px solid var(--line);\n  box-shadow: 4px 4px 0 var(--shadow);\n  padding: 1rem 1.1rem;\n  display: flex;\n  flex-direction: column;\n  gap: 0.35rem;\n  justify-content: space-between;\n  text-align: center;\n}' in css, '')
    check('home: combined clubs card CSS (stacked link rows + divider)',
          '.stat-clubs {\n  padding: 0;\n  gap: 0;\n}' in css
          and '.stat-clubs .stat-link {\n  display: flex;\n  flex-direction: column;\n  gap: 0.35rem;\n  text-decoration: none;\n  padding: .7rem 1.1rem;\n  border: 2px solid transparent;\n  transition: transform 0.12s ease, background 0.12s ease, box-shadow 0.12s ease;\n}' in css
          and '.stat-clubs .stat-link + .stat-link { border-top: 2px solid var(--line); }' in css, '')
    check('home: stages card CSS (two parts + divider)',
          '.stat-stages {\n  padding: 0;\n  gap: 0;\n}' in css
          and '.stat-stages .stat-part {\n  display: flex;\n  flex-direction: column;\n  gap: 0.35rem;\n  text-decoration: none;\n  padding: .7rem 1.1rem;\n  border: 2px solid transparent;\n  transition: transform 0.12s ease, background 0.12s ease, box-shadow 0.12s ease;\n}' in css
          and '.stat-stages .stat-part + .stat-part { border-top: 2px solid var(--line); }' in css
          and '<div class="stat-card stat-stages">' in html
          and '<span class="stat-label">Stages Driven</span>' in html
          and '<span class="stat-label">Survival Rate</span>' in html, '')
    check('home: distance card CSS (km + miles parts + divider)',
          '.stat-distance {\n  padding: 0;\n  gap: 0;\n}' in css
          and '.stat-distance .stat-part {\n  display: flex;\n  flex-direction: column;\n  gap: 0.35rem;\n  text-decoration: none;\n  padding: .7rem 1.1rem;\n  border: 2px solid transparent;\n  transition: transform 0.12s ease, background 0.12s ease, box-shadow 0.12s ease;\n}' in css
          and '.stat-distance .stat-part + .stat-part { border-top: 2px solid var(--line); }' in css
          and '<div class="stat-card stat-distance">' in html
          and '<span class="stat-label">Kilometers Driven</span>' in html
          and '<span class="stat-label">Miles Driven</span>' in html, '')
    check('home: combined clubs card per-row hover (top half: top radii + scale 1.06 + card border/bg/shadow)',
          '.stat-clubs .stat-link:hover {\n  border-top-left-radius: .3rem;\n  border-top-right-radius: .3rem;\n  transform: scale(1.06);\n  z-index: 1;\n  border: 2px solid var(--line);\n  box-shadow: 4px 4px 0 var(--shadow);\n  background: var(--card);\n  box-shadow: inset 0 0 18px -8px rgba(175, 175, 175, 0.6), 4px 4px 0 var(--shadow), 0 8px 20px rgba(0, 0, 0, 0.35);\n}' in css, '')
    check('home: combined clubs card per-row hover (bottom half: bottom radii, top radii zeroed)',
          '.stat-clubs .stat-link + .stat-link:hover {\n  border-top-left-radius: 0;\n  border-top-right-radius: 0;\n  border-bottom-left-radius: .3rem;\n  border-bottom-right-radius: .3rem;\n  transform: scale(1.06);\n  z-index: 1;\n  border: 2px solid var(--line);\n  box-shadow: 4px 4px 0 var(--shadow);\n  background: var(--card);\n  box-shadow: inset 0 0 18px -8px rgba(175, 175, 175, 0.6), 4px 4px 0 var(--shadow), 0 8px 20px rgba(0, 0, 0, 0.35);\n}' in css, '')
    r = client.get('/drivers')
    drv_html = r.get_data(as_text=True)
    check('drivers page renders with the smoke driver',
          r.status_code == 200 and 'Smoke Driver' in drv_html
          and '<th>Stages driven</th><th>Stages completed</th><th>Distance</th>' in drv_html,
          f'status={r.status_code}')
    # #56: /drivers stats are computed from stage_runs (the stored users
    # counters are retired) — the smoke driver's clean rows (48210 + 1200 m)
    # are what shows up, not the old double-counted user columns.
    check('drivers page: distance comes from stage_runs (#56)',
          '49.4 km' in drv_html, '')
    # Stages completed is a % (2 of the smoke driver's 5 runs finished).
    check('drivers page: stages completed shown as a percentage',
          '<td>40%</td>' in drv_html, '')
    prof = client.get('/profile/smokedriver').get_data(as_text=True)
    check('profile: per-device distance list (#57)',
          'Input devices' in prof and 'Simucube 2 Pro (100%)' in prof, '')
    check('profile: two-column driving table (favorites + stages/distance paired)',
          '<table class="driving">' in prof
          and '<span class="stat-key">Favorite Car:</span>' in prof
          and '<span class="stat-key">Stages driven:</span>' in prof
          and '(40% completed)' in prof, '')
    drv_css = client.get('/static/css/style.css').get_data(as_text=True)
    check('profile: driving table CSS (two columns, divider, top lists)',
          '.driving td + td { border-left: 1px solid var(--line); }' in drv_css
          and '.driving .top-list { margin: 0; padding: 0; list-style: none; }' in drv_css, '')
    check('profile: top-list rows name left / distance right (flex markup + CSS)',
          '<span class="top-name">' in prof and '<span class="top-km num">' in prof
          and '><span class="top-km num">(' not in prof
          and '.driving .top-list li { display: flex; justify-content: space-between; align-items: baseline; gap: 0.6rem; padding: 0.12rem 0.35rem; font-size: 0.92rem; }' in drv_css
          and '.driving .top-list .top-km { margin-left: auto; white-space: nowrap; }' in drv_css, '')
    check('profile: top-list zebra striping matches home recent-times color',
          '.driving .top-list li:nth-child(even) { background: rgba(24, 28, 20, 0.25); }' in drv_css, '')
    check('profile: play counters as chip-warn chips row after the table',
          '<div class="chips-row play-chips">' in prof
          and '>Days played: 12</span>' in prof
          and '>Time played: 12h 0m</span>' in prof
          and '>Sessions: 7</span>' in prof, '')
    check('profile: play-chips CSS (one line, equal spacing around + between)',
          '.play-chips { justify-content: space-evenly; margin: 0.8rem 0 0; }'
          in client.get('/static/css/style.css').get_data(as_text=True), '')

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
    check('install: certificate/SSL troubleshooting panel',
          'id="cert-troubleshooting"' in html
          and 'CERTIFICATE_VERIFY_FAILED' in html
          and 'built-in Let\'s Encrypt' in html
          and 'Get-ChildItem Cert:\\CurrentUser\\CA,Cert:\\LocalMachine\\CA' in html
          and 'check your Windows date and time' in html, '')

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

    dash_css = client.get('/static/css/style.css').get_data(as_text=True)
    dash_html = client.get('/dashboard').get_data(as_text=True)
    check('dashboard: live-events table class + column borders + driver dot CSS',
          'class="live-events"' in dash_html
          and '.live-events td:not(:first-child) { border-left: 1px solid var(--line); }' in dash_css
          and 'td:has(.driver-dot) { display: flex; justify-content: center; }' in dash_css
          and 'width: 2rem; height: 2rem; border-radius: 50%;' in dash_css
          and '.table-wrap td { padding: 0.5rem 0.7rem; vertical-align: middle; }' in dash_css
          and '.dot-red { background: #e05252; box-shadow: 0 0 14px rgba(224, 82, 82, 0.75); }' in dash_css
          and '.dot-yellow { background: #e8c33f; box-shadow: 0 0 14px rgba(232, 195, 63, 0.75); }' in dash_css
          and '.dot-green { background: #71b348; box-shadow: 0 0 14px rgba(113, 179, 72, 0.75); }' in dash_css, '')

    # join/leave forms (logged-in only): next field must carry the prefixed
    # list URL + active filters so the post-action redirect lands on /clubs,
    # not a bare /clubs path that 404s under the VPS subpath.
    auth_clubs = client.get('/clubs?active=1').get_data(as_text=True)
    check('clubs: join/leave next field carries prefixed list URL + filters',
          'name="next" value="/clubs?active=1"' in auth_clubs, '')

    with app.app_context():
        club = Club.query.first()
        events = Event.query.all()
        club_id = club.id if club else None
        event_ids = [e.id for e in events]

    if club_id:
        r = client.get(f'/clubs/{club_id}')
        check(f'auth GET /clubs/{club_id}', r.status_code == 200, f'status={r.status_code}')
        det = r.get_data(as_text=True)
        check('club detail: tabbed menu bar (Events/Members) with Events active by default',
              'class="club-menu"' in det
              and '<span class="club-title">' in det
              and '<a class="active" href="#tab-events" data-tab="events">Events</a>' in det
              and '<a href="#tab-members" data-tab="members">Members</a>' in det
              and 'id="tab-members" hidden' in det, f'status={r.status_code}')
        check('club detail: members tab carries Gold/Silver/Bronze podium columns',
              '<th>Gold</th><th>Silver</th><th>Bronze</th>' in det, f'status={r.status_code}')
        check('club detail: champ arrows step left=N-1 (older) / right=N+1 (newer)',
              "document.getElementById('champ-prev').addEventListener('click', function () {\n"
              '    if (state.champ_index < state.selectors.length - 1) {\n'
              '      state.champ_index += 1;' in det
              and "document.getElementById('champ-next').addEventListener('click', function () {\n"
              '    if (state.champ_index > 0) {\n'
              '      state.champ_index -= 1;' in det, f'status={r.status_code}')
        board_css = client.get('/static/css/style.css').get_data(as_text=True)
        check('club detail: champ selector CSS (centered row, square arrows, tight margins)',
              '.champ-selector { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.3rem; }' in board_css
              and '.champ-arrow { flex: none; width: 2.8rem; height: 2.8rem; display: flex; align-items: center; justify-content: center; background: var(--card); border: 2px solid var(--line); color: var(--text); cursor: pointer; font-size: 0.9rem; margin: 0; }' in board_css
              and '.champ-center { flex: 1 1 auto; display: flex; align-items: baseline; gap: 0.7rem; flex-wrap: wrap; background: var(--card); border: 2px solid var(--line); padding: 0.4rem 0.8rem; align-content: center; }' in board_css
              and '.champ-window { color: var(--muted); font-size: 0.85rem; }' in board_css, '')
        check('club detail: champ arrows disabled at the boundaries',
              "document.getElementById('champ-prev').disabled = state.champ_index >= state.selectors.length - 1;" in det
              and "document.getElementById('champ-next').disabled = state.champ_index <= 0;" in det
              and '.champ-arrow:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }' in board_css
              and '.champ-arrow:disabled { color: var(--line); cursor: default; }' in board_css, '')
        check('club detail: round boxes show location thumb + top-left class badge (JS + CSS)',
              '<span class="round-thumb-wrap">' in det
              and "(r.class_thumb_url ? '<img class=\"round-class-thumb\" src=\"' + esc(r.class_thumb_url) + '\" alt=\"\">' : '')" in det
              and '<span class="round-class">' not in det
              and '.round-box {\n  flex: none;\n  width: 33%;\n  text-align: left;\n  background: var(--card);\n  border: none;\n  padding: 0 0;\n  cursor: pointer;\n  display: flex;\n  flex-direction: column;\n}' in board_css
              and '.round-box.active {\n  background: var(--accent-deep);\n}' in board_css
              and '.round-strip { display: flex; gap: 0.6rem; overflow-x: auto; padding-bottom: 0.3rem; margin-bottom: 0.8rem; scrollbar-width: thin; scrollbar-color: var(--line) rgba(24, 28, 20, 0.5); }' in board_css
              and '.round-strip::-webkit-scrollbar { height: 10px; }' in board_css
              and '.round-strip::-webkit-scrollbar-track { background: rgba(24, 28, 20, 0.5); }' in board_css
              and '.round-strip::-webkit-scrollbar-thumb { background: var(--line); border: 2px solid var(--card); }' in board_css
              and '.round-strip::-webkit-scrollbar-thumb:hover { background: var(--muted); }' in board_css
              and '.round-thumb-wrap { position: relative; display: block; margin-bottom: 0.25rem; }' in board_css
              and '.round-thumb { display: block; width: 100%; aspect-ratio: 736 / 347; object-fit: cover; }' in board_css
              and '.round-class-thumb {\n  position: absolute;\n  top: 0;\n  left: .5rem;\n  width: min(4.5rem, 20%);\n}' in board_css
              and '.round-window { color: var(--muted); font-size: 0.8rem; }' in board_css
              and 'details.create-club .plus, .tyre-chip, .champ-center, .round-thumb, .round-class-thumb { border-radius: 0.3rem; }' in board_css, '')
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
