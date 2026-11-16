"""Dump the embedded board-state of a club detail page from an existing web DB.

Usage: python tools/dump_club_board_state.py <db_path> <club_id> [username]"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
WEB = HERE / 'dr3' / 'web'

DB_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else WEB / 'smoke.db'
CLUB_ID = sys.argv[2] if len(sys.argv) > 2 else None
USERNAME = sys.argv[3] if len(sys.argv) > 3 else None

os.environ['SECRET_KEY'] = 'dump-key'
os.environ['DATABASE_URL'] = f'sqlite:///{DB_PATH}'
os.environ['DR3_DISABLE_CSRF'] = '1'
sys.path.insert(0, str(WEB))

from app import create_app  # noqa: E402

app = create_app()
client = app.test_client()
if USERNAME:
    client.post('/login', data={'username': USERNAME, 'password': 'password123'})
html = client.get(f'/clubs/{CLUB_ID}').get_data(as_text=True)
m = re.search(r'id="board-state">(.*?)</script>', html, re.S)
bs = json.loads(m.group(1)) if m else {}
print('event_id:', bs.get('event_id'))
b = bs.get('board') or {}
print('board keys:', list(b.keys())[:12])
s = bs.get('standings')
print('standings type:', type(s).__name__)
print(str(s)[:500])
