"""Reproduce a club-detail page error against an existing web DB (e.g. the
smoke test's leftover smoke.db) and print the full server traceback.

Usage: python tools/repro_club_detail_500.py <db_path> <club_id> [username]

Logs in as [username] (optional — anonymous otherwise), GETs /clubs/<club_id>
with exception propagation enabled, prints the status or the traceback."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
WEB = HERE / 'dr3' / 'web'

DB_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else WEB / 'smoke.db'
CLUB_ID = sys.argv[2] if len(sys.argv) > 2 else None
USERNAME = sys.argv[3] if len(sys.argv) > 3 else None

os.environ['SECRET_KEY'] = 'repro-key'
os.environ['DATABASE_URL'] = f'sqlite:///{DB_PATH}'
os.environ['DR3_DISABLE_CSRF'] = '1'
os.environ.pop('SESSION_COOKIE_SECURE', None)
sys.path.insert(0, str(WEB))

from app import create_app  # noqa: E402

app = create_app()
app.testing = True
app.config['PROPAGATE_EXCEPTIONS'] = True
client = app.test_client()

if not CLUB_ID:
    with app.app_context():
        from app.models import Club
        clubs = Club.query.all()
        print('clubs:', [(c.id, c.name) for c in clubs])
        sys.exit(1)

if USERNAME:
    r = client.post('/login', data={'username': USERNAME, 'password': 'password123'})
    print('login:', r.status_code)

try:
    r = client.get(f'/clubs/{CLUB_ID}')
    print('GET /clubs/%s -> %s' % (CLUB_ID, r.status_code))
    if r.status_code != 200:
        print(r.get_data(as_text=True)[:3000])
except Exception:
    traceback.print_exc()
    sys.exit(1)
