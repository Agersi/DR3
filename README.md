# DR3 — Dirt Rally 3.0

A self-hosted club server for **DiRT Rally 2.0**. DR3 replaces the official
RaceNet/EgoNet backend with your own: players run a small local server on
their PC, and clubs, championships, leaderboards and stage results are served
by your site instead of Codemasters'.

Live instance: <https://byagersi.com/rally>

## What it does

- **Clubs & championships** — create rally and rallycross events with a web
  builder: per-championship scoring (WRC, Group B, or Codemasters
  field-size formula), power-stage bonus points, Multi-Location Rally (one
  car-select, vehicle state carried between rallies) and Rally Season modes.
- **In-game client** — `DR3.exe` (Windows): manages the hosts redirect + TLS
  cert, runs the local proxy server, verifies your game token, and logs
  everything to a GUI with Main / Overlay / Logs tabs.
- **Streaming text files** — while the server runs, DR3 can write OBS/SimHub
  text files (club, event, stage name, stage counter, car, class, top-5
  leaderboard) for stream overlays.
- **Website** — Flask app with club/event pages, championship standings and
  per-round results, account + game-token management, and the game-facing
  REST API the local server calls.

## Repository layout

```
dr3/                  client + web application
  dr3_app.py          Windows GUI entry point (Tk)
  dr2server/          local proxy server: EgoNet protocol codec, RPC
                      dispatcher, account store, streaming file writer
  web/                Flask app (club site + game API), templates, statics
  data/               upstream protocol templates bundled with the exe
  scripts/            dev utilities (cert generation, etc.)
  tools/              GUI checks and repro scripts
  build_exe.py        PyInstaller build -> dist/DR3-windows.zip
  VERSION             client version (also embedded in the exe)
tools/                project-level helpers: VPS/SFTP deploy scripts,
                      protocol probes, regression repros
```

## Getting the client

Download the latest `DR3-windows.zip` from <https://byagersi.com/rally>
(served at `/download/DR3-windows.zip`), extract it anywhere, run
`DR3.exe`, paste your game token and press **Start**. Then launch DiRT
Rally 2.0 via Steam.

## Running the web app

Requires Python 3.11+ and MySQL:

```
cd dr3/web
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install flask flask-wtf flask-sqlalchemy gunicorn pymysql
copy .env.example .env                             # fill in real values
flask --app app run                                # dev server
```

`.env` variables: `SECRET_KEY`, `DATABASE_URL` (mysql+pymysql), `SITE_URL`
(must include the `/rally` subpath when hosted under a prefix),
`SESSION_COOKIE_SECURE`, `CRON_API_KEY`, and `EMAIL_*` for mail.

## Building the client exe

```
cd dr3
python -m venv .venv-build && .venv-build\Scripts\activate
pip install pyinstaller cryptography
python build_exe.py          # -> dist/DR3-windows.zip
```

## Tests & checks

- `dr3/web/smoke_test.py` — end-to-end web smoke (standings, results, API)
- `dr3/web/render_check.py` — renders every page and asserts 200
- `tools/repro_*.py` — regression repros for specific bug fixes
- `dr3/tools/check_tab_scroll.py` — GUI scroll behaviour check
