"""Render check: GET every page and assert expected status codes.

Run AFTER smoke_test.py (it reuses smoke.db and the smokedriver account it creates).
    .venv\\Scripts\\python.exe smoke_test.py
    .venv\\Scripts\\python.exe render_check.py
"""
from __future__ import annotations

import json
import os
import re
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
                  ('/forgot_password', 200), ('/vehicles', 200), ('/cars', 302), ('/leaderboard', 200),
                  ('/static/css/style.css', 200),
                  ('/static/logo.png', 200), ('/nope', 404)]
    for path, want in anon_pages:
        r = client.get(path)
        check(f'anon GET {path}', r.status_code == want, f'status={r.status_code}')

    # leaderboard: filter form + server-rendered board with data-* hydration
    lb_html = client.get('/leaderboard').get_data(as_text=True)
    check('leaderboard: filter form + times board headers + hydration script',
          '<form class="panel lb-filters" method="get"' in lb_html
          and 'id="lb-board-table"' in lb_html
          and           '<th class="car-col">Vehicle</th><th>Tyres</th><th>Spares</th><th>Input</th><th>Camera</th><th class="src-cell">Source</th>' in lb_html
          and "tbody.querySelectorAll('tr').forEach(applyTuning);" in lb_html, '')
    check('site nav: vehicles link between clubs and leaderboard',
          '<a href="/vehicles">Vehicles</a>' in lb_html
          and '<a href="/leaderboard">Leaderboard</a>' in lb_html
          and lb_html.find('/clubs">Clubs') < lb_html.find('/vehicles">Vehicles') < lb_html.find('/leaderboard">Leaderboard'), '')
    # stage=11 has smokedriver bests: car 2 with splits [101.0, 100.5], car 9
    # without — exercises both the value cells and the club-board fallback.
    lb_rows = client.get('/leaderboard?stage=11').get_data(as_text=True)
    check('leaderboard: splits columns always after Diff 1st (club-board fallback)',
          '<th class="num diff-col">Diff 1st</th><th class="num split-col">Split #1</th>' in lb_rows
          and '<td class="num split-col">101.00</td>' in lb_rows
          and '<span class="dash-tip" data-tip="Configure Telemetry">-</span>' in lb_rows, '')
    # stage=13 has a Rally GT car (565) — the vehicle cell carries the class thumbnail.
    _lb_cls = client.get('/leaderboard?stage=13').get_data(as_text=True)
    check('leaderboard: class image in front of the vehicle name + car page link',
          '<td class="car-cell car-col"><img class="class-thumb" src="/static/classes/rally-gt.png" alt="" loading="lazy"> <a class="driver-link" href="/vehicles?vehicle=565">Porsche 911 RGT Rally Spec</a></td>' in _lb_cls, '')
    check('leaderboard: tyre cell carries damage payload + weather for the popup',
          'data-td="' in lb_rows and 'data-cond="' in lb_rows
          and "dmg.className = 'dmg-popup'" in lb_rows
          and 'No damage data for this stage.' in lb_rows, '')
    check('leaderboard: tyre images carry data-tdx (popup indexes the set arrays)',
          '" data-tdx="' in lb_rows, '')
    check('leaderboard: mode toggle + filters button + vehicle column always visible',
          'class="lb-mode-toggle"' in lb_html
          and '<option value="">All cars</option>' in lb_html
          and 'id="lb-more-btn"' in lb_html
          and 'class="lb-extra-inner"' in lb_html
          and '<th class="car-col">Vehicle</th>' in client.get('/leaderboard?car=999999').get_data(as_text=True), '')
    from dr2server.game_data import VEHICLE_CLASSES as _VCS
    _cls_cid = next(iter(_VCS))
    check('leaderboard: class options select every car of the class',
          'value="class-' in lb_html
          and '(all cars)' in lb_html
          and client.get('/leaderboard?car=class-999999').status_code == 200
          and '<th class="car-col">Vehicle</th>' in client.get(f'/leaderboard?car=class-{_cls_cid}').get_data(as_text=True), '')
    # strict tag equality: a stray char after `selected` (e.g. `selected}`)
    # silently kills the default selection in browsers
    check('leaderboard: car/class selection renders a clean selected attribute',
          '<option value="572" selected>' in client.get('/leaderboard?car=572').get_data(as_text=True)
          and f'<option value="class-{_cls_cid}" selected>' in client.get(f'/leaderboard?car=class-{_cls_cid}').get_data(as_text=True), '')
    check('leaderboard: distance mode swaps the board columns',
          client.get('/leaderboard?mode=distance').get_data(as_text=True)
          .count('<th class="num">Stages</th><th class="num">Distance</th>') == 1
          and '<th>Tyres</th>' not in client.get('/leaderboard?mode=distance').get_data(as_text=True), '')
    lb_css = client.get('/static/css/style.css').get_data(as_text=True)
    check('leaderboard: filter panel CSS',
          '.lb-filters { margin-bottom: 0.6rem; padding: 1rem; }' in lb_css
          and           '.lb-filter-row { display: flex; gap: 0.9rem; flex-wrap: wrap; }' in lb_css
          and '.lb-mode-toggle label:has(input:checked) { background: var(--accent); color: var(--bg); }' in lb_css
          and '.lb-filters-btn { margin: 0 0 .2rem 0; padding: .2rem .53rem; align-self: flex-end; font-size: .9rem; }' in lb_css
          and '.lb-filters .cselect-btn { position: relative; width: 100%; padding: 0.5rem 2.1rem 0.5rem 0.6rem;' in lb_css, '')
    check('leaderboard: stage/car select widths + animated extra row (grid 0fr->1fr)',
            '.lb-filter-row .cselect:has(select[name=loc]), .lb-filter-row .cselect:has(select[name=stage]), .lb-filter-row .cselect:has(select[name=car]) { width: 27.2rem; }' in lb_css
          and '.lb-extra-row { display: grid; grid-template-rows: 0fr; margin-top: 0; transition: grid-template-rows 0.15s ease; }' in lb_css
          and '.lb-extra-row.open { grid-template-rows: 1fr; }' in lb_css, '')
    check('leaderboard: diff-col width + centered source + popup weather line (CSS)',
          '#lb-board-table .diff-col { width: 5.5rem; }' in lb_css
          and '#lb-board-table .src-cell { text-align: center; }' in lb_css
          and '.dmg-cond { margin-top: 0.6rem;' in lb_css
          and '#lb-board-table .car-col { width: 14.5rem; }' in lb_css
          and '#lb-board-table th.car-col { padding-left: 51px; }' in lb_css
          and '#lb-board-table .pen-col { width: 4.5rem; }' in lb_css, '')
    # Rallycross stages (track 641 = Bikernieki) carry no penalty column —
    # the board is about best laps there; smoke fixtures keep 641 empty.
    _rx_lb = client.get('/leaderboard?stage=641').get_data(as_text=True)
    check('leaderboard: penalty column on rally stages, absent on RX',
          '<th class="num pen-col">Penalty</th>' in lb_html
          and 'pen-col' not in _rx_lb
          and '<td colspan="10">No times yet.</td>' in _rx_lb, '')
    # The stage filter is split into Location + Stage selects; picking an RX
    # location auto-selects its only stage (track 639 = Mettet).
    def _stage_select(page_html):
        i = page_html.find('<select name="stage">')
        return page_html[i:page_html.find('</select>', i)] if i >= 0 else ''
    check('leaderboard: location + stage split into two selects, locations grouped by discipline (no optgroups in the stage list)',
          '<label id="lb-loc-label">Location' in lb_html
          and '<select name="loc">' in lb_html
          and '<optgroup label="Rally">' in lb_html
          and '<optgroup label="Rallycross">' in lb_html
          and 'optgroup' not in _stage_select(lb_html)
          and 'id="lb-loc-label"' in client.get('/leaderboard?mode=distance').get_data(as_text=True), '')
    _rx_loc_html = client.get('/leaderboard?loc=39').get_data(as_text=True)  # Mettet RX
    check('leaderboard: RX location auto-selects its only stage',
          _stage_select(_rx_loc_html).count('<option') == 1
          and '<option value="639" selected>' in _stage_select(_rx_loc_html), '')
    # The form always submits BOTH selects: a just-picked Location still carries
    # the previous Stage — the picked location must win that conflict.
    _conf_html = client.get('/leaderboard?loc=39&stage=612').get_data(as_text=True)  # Mettet + stale Argentina stage
    check('leaderboard: picked location wins over a stale stage from another location',
          '<option value="639" selected>' in _stage_select(_conf_html)
          and '<option value="39" selected>' in _conf_html, '')
    check('split coloring: column best green, others red (leaderboard JS + CSS)',
          '.split-best { color: var(--muted); }' in lb_css
          and '.split-worse { color: var(--warning); }' in lb_css
          and "c[1] === best ? 'split-best' : 'split-worse'" in lb_html, '')

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
    reg_html = client.get('/register').get_data(as_text=True)
    check('register page styling (register-only centered h1 without stripe, 550px auth-card, soft panel shadow, centered register button)',
          'h1 {\n  font-family: var(--display); text-transform: uppercase; letter-spacing: 0.04em;\n  font-size: clamp(1.8rem, 4vw, 2.3rem); line-height: 1.05; font-weight: 700;\n  margin: 1.4rem 0 0.8rem; display: flex; align-items: center; gap: 0.6rem; width: 100%;\n}' in css
          and "h1::before { content: ''; width: 2em;" in css
          and '.register-title {\n  display: block;\n  font-family: var(--display);\n  text-transform: uppercase;\n  letter-spacing: 0.04em;\n  font-size: clamp(1.8rem, 4vw, 2.3rem);\n  line-height: 1.05;\n  font-weight: 700;\n  margin: 0 1rem 1rem;\n  text-align: center;\n}' in css
          and '.register-title::before { content: none; }' in css
          and '.auth-card { max-width: 550px; margin: 2rem auto; }' in css
          and '.register-btn { display: block; margin: .8rem auto; }' in css
          and '.panel {\n  background: var(--card); border: 2px solid var(--line);\n  box-shadow: 4px 4px 4px var(--shadow);\n  padding: 1.1rem 1.25rem; margin: 1rem 0;\n}' in css
          and 'auth-logo' not in reg_html
          and '<h1 class="register-title">Create account</h1>' in reg_html
          and '<button type="submit" class="register-btn">Register</button>' in reg_html, '')
    check('custom dropdown widget (all-browser listbox: 500px cap, accent hover/focus, recent-box scrollbar)',
          '.cselect { position: relative; display: block; }' in css
          and 'max-height: 500px;' in css
          and 'scrollbar-color: var(--line) rgba(24, 28, 20, 0.5);' in css
          and '.cselect-item:hover, .cselect-item:focus, .cselect-item.cselect-focus { background: var(--accent); color: var(--bg); }' in css
          and '.cselect.open .cselect-menu { transform: scaleY(1); visibility: visible; pointer-events: auto; }' in css
          and 'position: fixed; z-index: 30;' in css
          and "menu.style.maxHeight = Math.max(60, Math.min(500, window.innerHeight - r.bottom - 8)) + 'px';" in html
          and "stroke='%23ECDFCC'" in css
          and 'color-scheme: dark;' in css
          and 'function enhance(sel)' in html
          and "sel.dataset.cselect === '1'" in html, '')
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
              and 'details.create-club .plus, .hover-tip, .champ-center, .round-thumb, .round-class-thumb, .dmg-popup { border-radius: 0.3rem; }' in css, '')

    # home recent-times box (backlog #42): server-rendered first page +
    # infinite-scroll JS wired to the public endpoint
    html = client.get('/').get_data(as_text=True)
    check('home: recent-times box with rows + scroll JS (club-style num column)',
          'recent-times-box' in html and 'recent-times-body' in html
          and 'Smoke Driver' in html
          and 'data-url="/api/home/recent-times"' in html
          and "addEventListener('scroll'" in html
          and '.recent-box .num { text-align: right; font-variant-numeric: tabular-nums; }' in css
          and '.recent-box th:first-child, .recent-box td:first-child { padding-left: .5rem; }' in css
          and '.recent-box thead th:first-child { padding-left: calc(40px + .5rem); }' in css, '')
    check('home: recent-times rows link driver + club + vehicle pages (all use driver-link)',
          '<a class="driver-link" href="/profile/smokedriver">Smoke Driver</a>' in html
          and "a.className = 'driver-link';" in html
          and '<a class="driver-link" href="/vehicles?vehicle=1">Vehicle 1</a>' in html, '')
    check('home: recent-times Time + Posted columns use num class (headers, server + JS rows)',
          '<th class="num">Time</th>' in html and '<th class="num">Posted</th>' in html
          and html.count('<td class="num">') == 4
          and "tr.appendChild(cell(row.time_text, 'num'));" in html
          and "tr.appendChild(cell(row.at_text, 'num'));" in html, '')
    check('home: recent-times Tyres/Spares/Input/Camera columns (data attrs + board-style cell JS)',
          '<th>Tyres</th>' in html and '<th>Spares</th>' in html
          and '<th>Input</th>' in html and '<th>Camera</th>' in html
          and 'class="tyre-cell" data-tyres="[]" data-td="[]"' in html
          and 'class="spare-cell" data-spares=""' in html
          and 'class="input-cell" data-input=""' in html
          and 'class="cam-cell" data-camera=""' in html
          and 'function tyreCell(' in html and 'function spareCell(' in html
          and 'function inputCell(' in html and 'function camCell(' in html
          and "tuningCells(row).forEach(function (td) { tr.appendChild(td); });" in html
          and "tbody.querySelectorAll('tr').forEach(applyTuning);" in html
          and '.recent-box th:nth-child(4), .recent-box td:nth-child(4)' in css, '')
    r = client.get('/cars/565', follow_redirects=True)
    check('car page renders name + class + tabs (old /cars/<id> redirects to the vehicle page)',
          r.status_code == 200 and 'Porsche 911 RGT Rally Spec' in r.get_data(as_text=True)
          and 'Rally GT' in r.get_data(as_text=True)
          and 'id="car-select"' in r.get_data(as_text=True)
          and '<option value="565" selected' in r.get_data(as_text=True),
          f'status={r.status_code}')
    check('car page fastest-stage leaderboards side by side (in-class + overall)',
          'class="car-fastest-duo"' in r.get_data(as_text=True)
          and 'id="car-fastest-class-body"' in r.get_data(as_text=True)
          and 'id="car-fastest-overall-body"' in r.get_data(as_text=True)
          and 'Fastest in class' in r.get_data(as_text=True)
          and 'Fastest overall' in r.get_data(as_text=True)
          and '.car-fastest-duo { display: flex; gap: 1rem; flex-wrap: wrap; }' in css
          and '.car-fastest-col { flex: 1 1 240px; min-width: 0; }' in css, '')
    check('car page column padding/alignment + "Distance by User" heading (meta removed)',
          '<h2>Distance by User</h2>' in r.get_data(as_text=True)
          and 'Total distance driven in this car, per driver.' not in r.get_data(as_text=True)
          and '.car-fastest th:first-child, .car-fastest td:first-child { padding-left: .5rem; }' in css
          and '.car-fastest th:last-child, .car-fastest td:last-child { width: 4rem; text-align: center; }' in css
          and '.car-distance th:first-child, .car-distance td:first-child { padding-left: .5rem; }' in css
          and '.car-distance thead th:first-child { padding-left: calc(40px + .5rem); }' in css
          and '.car-distance th:last-child, .car-distance td:last-child { text-align: right; }' in css
          and 'table th:last-child, table td:last-child { padding-right: .5rem; }' in css
          and '.car-fastest tr.fastest-detail td:last-child { padding-right: 0; }' in css, '')
    check('car page fastest tables: rows expand to linked stage names (in-class link carries the class, overall does not)',
          'class="fastest-row"' in r.get_data(as_text=True)
          and '<tr class="fastest-detail"><td colspan="2"><div class="fd-wrap"><div class="fd-inner"><a class="driver-link" href="/leaderboard?stage=636&amp;car=class-107">Tolt Valley Sprint Forward</a></div></div></td></tr>' in r.get_data(as_text=True)
          and '<tr class="fastest-detail"><td colspan="2"><div class="fd-wrap"><div class="fd-inner"><a class="driver-link" href="/leaderboard?stage=636">Tolt Valley Sprint Forward</a></div></div></td></tr>' in r.get_data(as_text=True)
          and '.car-fastest tbody tr.fastest-row { cursor: pointer; font-weight: 600; user-select: none; }' in css
          and '.car-fastest tr:nth-child(even) td { background: transparent; }' in css
          and '.fd-wrap { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.15s ease; }' in css
          and '.car-fastest tr.fastest-detail.open .fd-wrap { grid-template-rows: 1fr; }' in css
          and '.stage-name' not in css, '')
    check('car page CSS (selector row + header flex + stats layout)',
          '.car-pick { display: flex; gap: 0.8rem; align-items: center; }' in css
          and '.car-header { display: flex; gap: 1.25rem; align-items: center; flex-wrap: wrap; }' in css
          and '.car-img { width: 50%; height: auto; }' in css
          and '.car-stats { display: flex; gap: 0.3rem 0; margin: 0; flex-direction: column; flex-wrap: wrap; max-height: 13rem; max-width: 18rem; align-content: space-between; }' in css
          and '.car-stat { width: 9rem; }' in css, '')
    check('car page header: spec stats + engine/drivetrain diagram',
          '<div class="car-stat"><dt>Car Class</dt><dd>Rally GT</dd></div>' in r.get_data(as_text=True)
          and '<div class="car-stat"><dt>Power</dt><dd>425 hp</dd></div>' in r.get_data(as_text=True)
          and 'id="car-diagram" data-engine="rear" data-drive="rear"' in r.get_data(as_text=True)
          and 'class="cd-wheel cd-fl"' in r.get_data(as_text=True)
          and 'cd-captions' not in r.get_data(as_text=True)
          and '.car-diagram[data-engine="mid"] .cd-engine { top: 50%; margin-top: 6px; }' in css
          and '.cd-body { position: relative; width: 80px; height: 150px; border: 2px solid var(--line); border-radius: 24px 24px 14px 14px; background: rgba(60, 61, 55, 0.5); }' in css
          and '.cd-wheel { position: absolute; width: 15px; height: 28px; background: var(--line); border-radius: 4px; }' in css
          and '.car-diagram[data-drive="4wd"] .cd-wheel { background: var(--muted); }' in css
          and '.car-diagram { display: flex; flex-direction: column; align-items: center; gap: 0.3rem; zoom: 1.7; margin: 0 .2rem; }' in css
          and '.car-info { flex: 1 1 100%; text-align: justify; margin: 0; line-height: 1.35; font-size: .9rem; font-family: var(--mono); letter-spacing: -0.035em; }' in css, '')
    check('recent-times header stays pinned while rows scroll (sticky thead + box scrollbar)',
          '.recent-box { max-height: 360px; overflow-y: auto; scrollbar-width: thin; scrollbar-color: var(--line) rgba(24, 28, 20, 0.5); }' in css
          and '.recent-box::-webkit-scrollbar { width: 10px; }' in css
          and '.recent-box::-webkit-scrollbar-thumb { background: var(--line); border: 2px solid var(--card); }' in css
          and '.recent-box thead th { position: sticky; top: 0; z-index: 1; }' in css
          and "box.addEventListener('scroll', function () {" in html, '')
    check('home: recent-times fixed column widths + single-line rows (Vehicle header)',
          '<th>Vehicle</th>' in html
          and '.recent-box table { table-layout: fixed; }' in css
          and '.recent-box th, .recent-box td { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }' in css
          and '.recent-box tbody tr { height: 1.8rem; }' in css
          and '.recent-box th:nth-child(1), .recent-box td:nth-child(1) { width: 12rem; }' in css
          and '.recent-box th:nth-child(3), .recent-box td:nth-child(3) { width: 13rem; }' in css
          and '.recent-box th:nth-child(9), .recent-box td:nth-child(9) { width: 4.5rem; }' in css, '')
    check('home: recent-times board-style hover tips (body-level fixed element on tbody)',
          "tip.className = 'hover-tip';" in html
          and "e.target.closest('[data-tip]')" in html
          and "tbody.addEventListener('mouseover'" in html
          and "tbody.addEventListener('mouseleave', hideTip);" in html
          and ".hover-tip {\n  position: fixed;" in css, '')
    check('home: recent-times tyre click opens the pinned damage popup (club-board parity)',
          "dmg.className = 'dmg-popup';" in html
          and 'function carSvg(' in html and 'function dmgBars(' in html
          and 'function renderDmg(' in html and 'function placeDmg(' in html
          and 'function openDmg(' in html and 'function closeDmg(' in html
          and "data-tdx=\"' + i + '\"><img src=\"" in html
          and "mk('tyre-cell', { tyres: JSON.stringify(row.tyres || []), td: JSON.stringify(row.tyre_damage || []) });" in html
          and "tbody.addEventListener('click'" in html
          and "if (e.key === 'Escape') closeDmg();" in html
          and "e.target.closest('.dmg-popup')" in html
          and 'No damage data for this stage.' in html
          and html.count("box.addEventListener('scroll', function () {") == 2, '')
    # data-td is tojson output inside a double-quoted attribute: raw quotes
    # would truncate the attribute and break the popup's JSON.parse. Decode it
    # exactly like the browser does (entity unescape -> parse) and compare.
    from html import unescape as _attr_unescape
    _td_m = re.search(r'data-tyres="\[2\]" data-td="([^"]*)"', html)
    try:
        _td_ok = _td_m is not None and json.loads(_attr_unescape(_td_m.group(1))) \
            == [{'s': 0, 'd': {'engine': 0.25, 'wheels_wear': 7}}]
    except (TypeError, ValueError):
        _td_ok = False
    check('home: recent-times data-td attr survives HTML escaping (popup JSON.parse round-trip)',
          _td_ok, '')
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
          and '<th class="num">Stages driven</th><th class="num">Stages completed</th><th class="num">Distance</th>' in drv_html,
          f'status={r.status_code}')
    # #56: /drivers stats are computed from stage_runs (the stored users
    # counters are retired) — the smoke driver's rows (48210 + 1200 m clean,
    # plus the two DNF close-outs' genuine partials 228330 + 32120 m) are what
    # shows up, not the old double-counted user columns.
    check('drivers page: distance comes from stage_runs (#56)',
          '309.9 km' in drv_html, '')
    # Stages completed is a % (4 of the smoke driver's 7 runs finished — the
    # board-cum fixture adds two clean rows for the car-resolution check).
    check('drivers page: stages completed shown as a percentage',
           '<td class="num">57%</td>' in drv_html, '')
    prof = client.get('/profile/smokedriver').get_data(as_text=True)
    check('profile: per-device distance list (#57)',
          'Input devices' in prof and 'Simucube 2 Pro (100%)' in prof, '')
    check('profile: two-column driving table (favorites + stages/distance paired)',
          '<table class="driving">' in prof
          and '<span class="stat-key">Favorite Car:</span>' in prof
          and '<span class="stat-key">Stages driven:</span>' in prof
          and '(57% completed)' in prof, '')
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
          and '<p>Make sure <strong>Start automatically when DR3 opens</strong> option in the Main tab is ticked.</p>' in html
          and 'start "" "C:\\DR3\\DR3.exe"' in html
          and '%*' in html
          and 'taskkill /im DR3.exe /f' in html
          and '"C:\\Users\\YOURNAME\\Desktop\\DR3.bat" %command%' in html
          and 'does not expand Windows environment variables' not in html, '')
    check('install: uninstall note (cert-troubleshooting panel removed)',
          'id="cert-troubleshooting"' not in html
          and '<p class="meta uninstall-note">If you want to uninstall DR3, go to the <strong>Logs</strong> tab and click <strong>Uninstall DR3</strong> at the bottom — it removes the program and your settings.</p>' in html
          and '<p class="meta small uninstall-note">(Press STOP first if the server is running.)</p>' in html
          and '.uninstall-note { margin: 0; }' in css, '')
    check('install: telemetry-apps panel removed (fixed port, nothing to configure)',
          'id="telemetry-apps"' not in html
          and 'telemetry_port' not in html
          and 'SimHub' not in html, '')

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
    check('dashboard: live-events table class + thead/tbody + club-style generic table CSS + driver dot CSS',
          'class="live-events"' in dash_html
          and '<thead>' in dash_html and '<tbody>' in dash_html
          and '.live-events td:not(:first-child) { border-left: 1px solid var(--line); }' not in dash_css
          and '.live-events th:nth-child(1), .live-events td:nth-child(1) { padding-left: .5rem; }' in dash_css
          and '.live-events th:nth-child(2), .live-events td:nth-child(2), .live-events th:nth-child(7), .live-events td:nth-child(7) { text-align: center; }' in dash_css
          and '<a class="driver-link" href="/clubs/' in dash_html
          and 'td:has(.driver-dot) { display: flex; justify-content: center; align-items: center; gap: 0.4rem; }' in dash_css
          and 'width: 1.5rem; height: 1.5rem; border-radius: 50%;' in dash_css
          and 'th, td { padding: 0.15rem 0.15rem; }' in dash_css
          and '.table-wrap { overflow-x: auto; background: var(--card); border: 2px solid var(--line); box-shadow: 4px 4px 4px var(--shadow); margin: 1rem 0; }' in dash_css
          and 'table.table, .table-wrap table { width: 100%; border-collapse: collapse; background: var(--card); margin: 0; font-size: 0.9rem; }' in dash_css
          and '.table-wrap thead th { border-bottom: 2px solid var(--line); color: var(--muted); font-size: 0.78rem; letter-spacing: 0.0em; }' in dash_css
          and '.table-wrap td { padding: .15rem; vertical-align: middle; }' in dash_css
          and ".table th, .table-wrap th {\n  font-family: var(--display); text-transform: uppercase; letter-spacing: 0.06em;\n  font-size: 0.82rem; font-weight: 600; background: var(--bg); color: var(--text);\n  text-align: left; border: 0;\n}" in dash_css
          and '.dot-red { background: #e05252; box-shadow: 0 0 14px rgba(224, 82, 82, 0.75); }' in dash_css
          and '.dot-yellow { background: #e8c33f; box-shadow: 0 0 14px rgba(232, 195, 63, 0.75); }' in dash_css
            and '.dot-green { background: #71b348; box-shadow: 0 0 14px rgba(113, 179, 72, 0.75); }' in dash_css, '')
    _tbl = dash_html[dash_html.find('class="live-events"'):dash_html.find('</table>', dash_html.find('class="live-events"'))]
    check('dashboard: started + done dots use board-style hover tips (no native title)',
          _tbl.count('data-tip=') >= 6 and 'title=' not in _tbl
          and 'have started the current event' in _tbl
          and 'are done with the current event' in _tbl, '')
    check('dashboard: driver-dot tooltip script matches the board hover tip',
          "tip.className = 'hover-tip';" in dash_html
          and "tbl.addEventListener('mouseover'" in dash_html, '')
    check('dashboard: live events no longer link to the removed event page',
          'href="/event/' not in dash_html, '')

    # join/leave forms (logged-in only): next field must carry the prefixed
    # list URL + active filters so the post-action redirect lands on /clubs,
    # not a bare /clubs path that 404s under the VPS subpath.
    auth_clubs = client.get('/clubs?active=1').get_data(as_text=True)
    check('clubs: join/leave next field carries prefixed list URL + filters',
          'name="next" value="/clubs?active=1"' in auth_clubs, '')

    with app.app_context():
        club = Club.query.filter_by(name='Smoke Crew').first()
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
              '<th class="num">Gold</th><th class="num">Silver</th><th class="num">Bronze</th>' in det, f'status={r.status_code}')
        check('club detail: champ arrows step left=N-1 (older) / right=N+1 (newer)',
              "document.getElementById('champ-prev').addEventListener('click', function () {\n"
              '    if (state.champ_index < state.selectors.length - 1) {\n'
              '      state.champ_index += 1;' in det
              and "document.getElementById('champ-next').addEventListener('click', function () {\n"
              '    if (state.champ_index > 0) {\n'
              '      state.champ_index -= 1;' in det, f'status={r.status_code}')
        board_css = client.get('/static/css/style.css').get_data(as_text=True)
        check('club detail: members + championships tables use the site table style (thead/tbody, right-aligned nums)',
              '<table class="table club-members">' in det
              and '<table class="table club-champs">' in det
              and '<thead>\n        <tr><th>Driver</th>' in det
              and '<a class="driver-link" href="/profile/' in det
              and '<thead>\n        <tr><th>Championship</th><th>Status</th><th class="num">Start</th><th class="num">End</th>' in det
              and '</tbody>' in det
              and '.club-members .num, .club-champs .num { text-align: right; font-variant-numeric: tabular-nums; }' in board_css
              and '.club-members th:first-child, .club-champs th:first-child, .club-members td:first-child, .club-champs td:first-child { padding-left: .5rem; }' in board_css
              and 'table th:last-child, table td:last-child { padding-right: .5rem; }' in board_css
              and '.club-members thead th:first-child { padding-left: calc(40px + .5rem); }' in board_css
              and '.club-members th:nth-child(2), .club-members td:nth-child(2) { text-align: center; }' in board_css
              and '.club-members form.inline { float: right; margin: .1rem; }' in board_css
              and '.club-members button.small { padding: .25rem; font-size: .75rem; }' in board_css, '')
        check('club detail: championships table — Ended chip, centered status col, delete button style',
              '<span class="chip">Ended</span>' in det
              and '<td class="num"><time class="js-local"' in det
              and '.club-champs th:nth-child(2), .club-champs td:nth-child(2) { text-align: center; }' in board_css
              and '.club-champs .btn.danger.small { padding: .25rem; margin: .1rem; font-size: .75rem; float: right; }' in board_css, '')
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
              and "<span class=\"round-loc\">' + esc(r.location || '(none)') + '</span>'" in det
              and "var wi = w.indexOf(' \u2192 ');" in det
              and "'<br>' + esc(w.slice(wi + 3))" in det
              and '.round-box {\n  flex: none;\n  width: 32.6%;\n  text-align: left;\n  background: var(--card);\n  border: none;\n  padding: 0 0;\n  cursor: pointer;\n  display: flex;\n  flex-direction: column;\n}' in board_css
              and '.mode-btn { background: var(--card); border: none; border-right: 2px solid var(--line); padding: 0.4rem 1rem; cursor: pointer; color: var(--accent); font-weight: 700; }' in board_css
              and '.round-box.active {\n  background: var(--accent-deep);\n}' in board_css
              and '.round-strip { display: flex; gap: 0.6rem; overflow-x: auto; padding-bottom: 0.3rem; margin-bottom: 0.8rem; scrollbar-width: thin; scrollbar-color: var(--line) rgba(24, 28, 20, 0.5); }' in board_css
              and '.round-strip::-webkit-scrollbar { height: 10px; }' in board_css
              and '.round-strip::-webkit-scrollbar-track { background: rgba(24, 28, 20, 0.5); }' in board_css
              and '.round-strip::-webkit-scrollbar-thumb { background: var(--line); border: 2px solid var(--card); }' in board_css
              and '.round-strip::-webkit-scrollbar-thumb:hover { background: var(--muted); }' in board_css
              and '.round-thumb-wrap { position: relative; display: block; }' in board_css
              and '.round-thumb { display: block; width: 100%; aspect-ratio: 736 / 347; object-fit: cover; filter: brightness(0.5) blur(3px); transition: filter 0.15s ease; }' in board_css
              and '.round-box:hover .round-thumb, .round-box.hovered .round-thumb, .round-box.active .round-thumb { filter: none; }' in board_css
              and "document.getElementById('round-strip').addEventListener('wheel', function (e) {" in det
              and 'var el = document.elementFromPoint(e.clientX, e.clientY);' in det
              and "setHoveredBox(el ? el.closest('.round-box') : null);" in det
              and '.round-class-thumb {\n  position: absolute;\n  top: 0;\n  left: .5rem;\n  width: min(4.5rem, 20%);\n  filter: brightness(0.5) blur(3px);\n  transition: filter 0.15s ease;\n}' in board_css
              and '.round-box:hover .round-class-thumb, .round-box.hovered .round-class-thumb, .round-box.active .round-class-thumb { filter: none; }' in board_css
              and '.round-loc {\n  font-weight: 900;\n  font-size: 0.95rem;\n  position: absolute;\n  bottom: 0;\n  left: 0;\n  right: 0;\n  height: 4.5rem;\n  border-bottom-left-radius: .3rem;\n  border-bottom-right-radius: .3rem;\n  padding-left: .5rem;\n  padding-top: .25rem;\n  color: var(--accent);\n  background: color-mix(in srgb, var(--accent-deep) 45%, transparent);\n}' in board_css
              and '.round-window {\n  color: var(--accent);\n  font-weight: 500;\n  font-size: 0.8rem;\n  position: absolute;\n  bottom: 0;\n  left: 0;\n  right: 0;\n  padding: .25rem .75rem;\n}' in board_css
                 and 'details.create-club .plus, .hover-tip, .champ-center, .round-thumb, .round-class-thumb, .dmg-popup { border-radius: 0.3rem; }' in board_css, '')
        check('club detail: splits toggle in thead reveals per-split/lap columns',
              '<thead id="board-head"></thead>' in det
              and "return { n: board.split_count || 0, word: board.is_rx ? 'Lap' : 'Split' };" in det
              and "'<th class=\"num split-col\">' + spec.word + ' #' + i" in det
              and "data-tip=\"Configure Telemetry\">-</span>" in det
              and '"splits-toggle"' in det
              and det.find("'<th class=\"num split-col\">'") < det.find("'<th class=\"expand-head\">'")
              and "data-tip=\"' + (open ? 'Hide Splits' : 'Show Splits') + '\"" in det
                and '.expand-head, .expand-cell { width: 2rem; text-align: center; white-space: nowrap; }' in board_css
              and ".expand-btn {\n  position: relative;\n  background: none;\n  border: none;\n  cursor: pointer;\n  color: var(--accent);\n  font-size: .8rem;\n  line-height: .8;\n  padding: .3rem;\n  margin: .4em;\n}" in board_css
              and '.hover-tip.show { opacity: 1; }' in board_css
              and "tip.className = 'hover-tip';" in det
              and "document.body.appendChild(tip);" in det
               and "e.target.closest('[data-tip]')" in det, '')
        check('club detail: Overall mode drops the expand/splits column and widens Time by 1rem',
              "if (state.mode === 'stage') {\n      ths += '<th class=\"expand-head\">'" in det
              and "var span = (state.mode === 'stage' ? 11 : 10) + (open ? spec.n : 0);" in det
              and "if (state.mode === 'stage') {\n        cells += '<td class=\"expand-cell\"></td>';" in det
              and ".classList.toggle('board--overall', state.mode === 'overall');" in det
               and 'table.board.board--overall th:nth-child(9), table.board.board--overall td:nth-child(9) { width: 6.5rem; }' in board_css, '')
        check('club detail: Overall mode stacks tyre sets with half-overlap in a wider Tyres column',
              '.board--overall .tyre-cell .tyre-img + .tyre-img { margin-left: -0.75em; }' in board_css
              and 'table.board.board--overall th:nth-child(4), table.board.board--overall td:nth-child(4) { width: 4.5rem; }' in board_css
              and "}).join('');" in det, '')
        check('club detail: board tooltips flow out of the scroll wrapper (body-level fixed element)',
              '.board-wrap { overflow-x: auto; scrollbar-width: thin; scrollbar-color: var(--line) rgba(24, 28, 20, 0.5); }' in board_css
               and 'position: fixed;' in board_css.split('.hover-tip {', 1)[1].split('}', 1)[0]
               and '.hover-tip::after' not in board_css, '')
        check('club detail: board table fills wrapper collapsed, grows to fit split columns when open',
              '.board-wrap::-webkit-scrollbar { height: 10px; }' in board_css
              and '.board-wrap::-webkit-scrollbar-track { background: rgba(24, 28, 20, 0.5); }' in board_css
              and '.board-wrap::-webkit-scrollbar-thumb { background: var(--line); border: 2px solid var(--card); }' in board_css
              and '.board-wrap::-webkit-scrollbar-thumb:hover { background: var(--muted); }' in board_css
              and 'width: max-content' not in board_css
              and 'table.board { table-layout: fixed; }' in board_css, '')
        check('club detail: splits state survives stage/round reloads and opening scrolls to the right',
              ".then(function (board) { state.board = board; renderAll(); })" in det
              and "state.splits_open = false; renderAll()" not in det
              and 'wrap.scrollLeft = wrap.scrollWidth;' in det, '')
        check('club detail: board rows are single-line (no cell wrapping)',
              '#board-table th, #board-table td { white-space: nowrap; }' in board_css, '')
        check('club detail: active round box uses card-mixed location bar',
              '.round-box.active .round-loc { background: color-mix(in srgb, var(--card) 65%, transparent); }' in board_css, '')
        check('club detail: initial load centers the active round box in the visible strip (only when not first)',
              'function centerActiveRound() {' in det
              and "var active = strip.querySelector('.round-box.active');" in det
              and 'if (!active || active === strip.children[0]) return;' in det
              and 'strip.scrollLeft = Math.max(0, strip.scrollLeft + (bl - sl) - (strip.clientWidth - active.offsetWidth) / 2);' in det
              and 'renderAll();\n  centerActiveRound();' in det, '')
        m = re.search(r'id="board-state">(.*?)</script>', det, re.S)
        board_state = json.loads(m.group(1)) if m else {}
        check('club detail: mode toggle is Stage-first with Stage the default mode (VR + Assists merged into Input)',
              det.find('data-mode="stage"') > -1
              and det.find('data-mode="stage"') < det.find('data-mode="overall"')
              and board_state.get('mode') == 'stage'
              and '<th>VR</th>' not in det
              and '<th>Assists</th>' not in det
              and 'colspan="13"' not in det
              and "inputCell(r.input, r.vr, r.assists)" in det
              and "INPUT_ICON = { wheel: 'wheel.png', controller: 'controller.png', keyboard: 'keyboard.png' };" in det
              and "if (vr === true) {\n      parts.push('<img class=\"input-icon\" src=\"' + STATIC + 'icons/vr.png\"" in det
              and "if (assists === true) {\n      parts.push('<img class=\"input-icon\" src=\"' + STATIC + 'icons/assist.png\"" in det
              and '.input-cell { white-space: nowrap; }' in board_css
              and '.input-icon { height: 1.1em; width: auto; vertical-align: -0.2em; }' in board_css
              and '.tyre-img { position: relative; display: inline-block; }' in board_css
              and 'split_count' in (board_state.get('board') or {})
              and 'is_rx' in (board_state.get('board') or {}), '')
        check('club detail: tyre column renders compound images with hover name tooltip',
              "var TYRE_FILE = {" in det
              and "1: '1_soft_gravel'" in det
              and "13: '13_dry_rx'" in det
              and "TYRE_FILE[t] || '#_unknown'" in det
                 and '<span class="tyre-img" data-tip="' in det
                 and ".hover-tip {\n  position: fixed;" in board_css, '')
        check('club detail: clicking a tyre image pins a vehicle-state damage popup (car model + % bars)',
              'data-tdx="' in det
              and "var dmg = document.createElement('div');" in det
              and "dmg.className = 'dmg-popup';" in det
              and "document.body.appendChild(dmg);" in det
              and 'function unpack4(v) {' in det
              and 'FL: { wear: 2, impact: 2, springs: 0, brakes: 0, dampers: 0, body: 0 }' in det
              and 'FR: { wear: 3, impact: 0, springs: 1, brakes: 1, dampers: 1, body: 3 }' in det
              and "['Engine', d.engine], ['Clutch', d.clutch], ['Radiator', d.radiator]," in det
              and 'viewBox="0 0 238 300"' in det
              and '<rect x="73" y="28" width="92" height="244" rx="15"/>' in det
              and 'rx="15" fill="#262b21"' in det
              and 'width="30" height="50" rx="11"' in det
              and 'var fh = Math.max(2, imp * 32);' in det
              and 'var ibx = x < 119 ? x + 17 : x - 23;' in det
              and 'height="32" rx="2" style="fill:var(--card)"' in det
              and 'width="60" height="44" rx="6"' in det
              and 'font-size="12"' in det
              and 'font-size="14"' in det
              and 'style="fill:var(--bg)"' in det
              and '<span class="dmg-title">Tyres: ' in det
              and '.dmg-bar-label { width: 3rem; flex: none; color: var(--muted); }' in board_css
              and 'No damage data for this stage.' in det
              and "var tyre = e.target.closest('.tyre-img');" in det
              and 'if (dmgTarget === tyre) closeDmg(); else openDmg(tyre);' in det
              and "if (e.key === 'Escape') closeDmg();" in det
              and "function renderRows(board) {\n    closeDmg();" in det
              and ".dmg-popup {\n  position: fixed;" in board_css
              and '.dmg-popup.show { opacity: 1; pointer-events: auto; }' in board_css
              and '.dmg-car { width: 9.6rem; flex: none; }' in board_css, '')
        check('club detail: spares column after Tyres shows the count with a tooltip (ink dash when unknown)',
              "function spareCell(v) {" in det
              and "<th>Tyres</th><th>Spares</th>" in det
              and "'<td class=\"spare-cell num\">' + spareCell(r.spares) + '</td>'" in det
              and "var tip = n === 0 ? 'No spare tyres' : (n + (n === 1 ? ' spare tyre' : ' spare tyres'));" in det
              and "return '<span data-tip=\"' + tip + '\">' + n + '</span>';" in det
              and "colspan=\"11\"" in det, '')
        check('club detail: driver flag merged into the driver cell (no own column)',
              '<th>#</th><th>Driver</th>' in det
              and 'colspan="12"' not in det
              and "'<td class=\"flag-cell\">'" not in det
              and "'<td>' + flagCell(r.flag_url, r.username) + '<a class=\"driver-link\" href=\"' + root + esc(r.driver_url) + '\">'" in det
              and '.flag { height: .95em; width: auto; vertical-align: -0.18em; margin-right: 0.3em; }' in board_css
              and '.driver-link { font-weight: 600; color: var(--ink); text-decoration: none; }' in board_css
              and '.driver-link:hover { color: var(--muted); }' in board_css, '')
        check('club detail: input column tooltip lists each shown icon on its own row',
              "tips.push(v.charAt(0).toUpperCase() + v.slice(1));" in det
              and "tips.push('VR');" in det
              and "tips.push('Assists');" in det
               and "data-tip=\"' + esc(tips.join('\\n'))" in det
               and "tip.style.whiteSpace = text.indexOf('\\n') >= 0 ? 'pre-line' : 'nowrap';" in det
               and '.input-tip { position: relative; display: inline-block; }' in board_css, '')
        check('club detail: tight board padding, centered tyre/input columns (incl. header), right-aligned penalty (incl. header)',
               'table.board th, table.board td { padding: 0.15rem 0.15rem; text-align: left; vertical-align: middle; overflow: hidden; text-overflow: ellipsis; }' in board_css
               and 'table.board th:nth-child(4), table.board td:nth-child(4), table.board th:nth-child(5), table.board td:nth-child(5), table.board th:nth-child(6), table.board td:nth-child(6) { text-align: center; }' in board_css
                and '.table-wrap thead th { border-bottom: 2px solid var(--line); color: var(--muted); font-size: 0.78rem; letter-spacing: 0.0em; }' in board_css
                and "table.board { table-layout: fixed; }" in board_css
               and "table.board th, table.board td { padding: 0.15rem 0.15rem; text-align: left; vertical-align: middle; overflow: hidden; text-overflow: ellipsis; }" in board_css
               and "table.board th:nth-child(1), table.board td:nth-child(1) { width: 2rem; text-align: center; }" in board_css
                 and all(f"table.board th:nth-child({n}), table.board td:nth-child({n}) {{ width: {w}; }}" in board_css
                         for n, w in ((2, '11.5rem'), (3, '11.5rem'), (4, '3rem'), (5, '3rem'), (6, '3rem'), (7, '3.5rem'), (8, '5.5rem'), (9, '5.5rem'), (10, '5.5rem')))
                and ".split-col { white-space: nowrap; width: 4rem; }" in board_css
                 and ".expand-head, .expand-cell { width: 2rem; text-align: center; white-space: nowrap; }" in board_css
                 and "'<th>Input</th><th>Camera</th><th class=\"num\">Penalty</th>'" in det
                  and "'<td class=\"num\">' + penaltyCell(r.penalty_ms) + '</td>'" in det, '')
        check('club detail: DNF tag sits in the Diff 1st cell, not the Time cell',
              "'<td class=\"num\">' + esc(r.time_text) + '</td>'" in det
              and "'<td class=\"num\">' + esc(r.diff_str) + (r.dnf ? ' <span class=\"dnf-tag\">DNF</span>' : '') + '</td>'" in det, '')
        check('club detail: DNF rows use the muted-red --warning color',
              '--warning: #C4635A;' in board_css
              and '.board-row.dnf td { color: var(--warning); }' in board_css, '')
        check('club detail: split cells colored green on column best, red otherwise',
              "Number(r.splits[i]) === bestOf[i] ? 'split-best' : 'split-worse'" in det
              and 'bestOf[bi] = bvals.length ? Math.min.apply(null, bvals) : null;' in det, '')
        check('club detail: empty board/standings cells use an ink dash (no muted)',
              "if (!tyres || !tyres.length) return '<span class=\"ink\">—</span>';" in det
              and "if (v == null) return '<span class=\"ink\">—</span>';" in det
              and "if (!parts.length) return '<span class=\"ink\">—</span>';" in det
              and "if (!v) return '<span class=\"ink\">—</span>';" in det
              and "if (!c || (!c.points && !c.bonus)) return '<span class=\"ink\">—</span>';" in det
              and '.ink { color: var(--ink); }' in board_css, '')
        check('club detail: championship standings table below the board (JS + CSS)',
                '<h2 class="standings-title">Championship Standings</h2>' in det
                and 'id="standings-table"' in det
                and "function renderStandings(st) {" in det
                and "' <span class=\"ps-bonus\">(+' + c.bonus + ')</span>'" in det
                and "root + '/clubs/' + state.club_id + '/championship-standings?'" in det
                and "ths += '<th class=\"num\"' + (r.location ? ' data-tip=\"' + esc(r.location) + '\"' : '') + '>' + esc(r.label) + '</th>';" in det
                and "['board-table', 'standings-table'].forEach(function (id) {" in det
                and 'table.standings th, table.standings td { text-align: left; white-space: nowrap; }' in board_css
                and 'table.standings .num { text-align: right; font-variant-numeric: tabular-nums; }' in board_css
                and 'table.standings th:nth-child(1), table.standings td:nth-child(1) { width: 2rem; text-align: center; }' in board_css
                and 'table.standings th:nth-child(2) { padding-left: 40px; }' in board_css
                and 'thead { height: 2rem; }' in board_css
                and '.ps-bonus { color: var(--accent); font-size: 0.85em; }' in board_css, '')
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
        check(f'old event page removed: GET /event/{eid} -> 404', r.status_code == 404, f'status={r.status_code}')

    print()
    if failures:
        print(f'RENDER CHECK FAILED: {len(failures)} failure(s)')
        return 1
    print('ALL RENDER CHECKS PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
