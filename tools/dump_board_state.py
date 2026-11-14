"""Dump the club-detail board-state JSON payload (diagnostic).

Usage: python tools/dump_board_state.py [club_id]
Reuses web/smoke.db like render_check.py — run smoke_test.py first.
With no club_id it picks the first club from /clubs.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / 'dr3' / 'web'
os.environ.setdefault('SECRET_KEY', 'smoke-test-key')
os.environ.setdefault('DATABASE_URL', f'sqlite:///{WEB / "smoke.db"}')
os.environ.setdefault('CRON_API_KEY', 'smoke-cron-key')
os.environ.setdefault('DR3_DISABLE_CSRF', '1')
sys.path.insert(0, str(WEB))

from app import create_app  # noqa: E402


def main() -> int:
    club_id = sys.argv[1] if len(sys.argv) > 1 else None
    app = create_app()
    client = app.test_client()
    if not club_id:
        m = re.search(r'/clubs/([A-Za-z0-9_-]+)', client.get('/clubs').get_data(as_text=True))
        club_id = m.group(1) if m else None
    if not club_id:
        print('no club found')
        return 1
    det = client.get(f'/clubs/{club_id}').get_data(as_text=True)
    m = re.search(r'id="board-state">(.*?)</script>', det, re.S)
    state = json.loads(m.group(1)) if m else {}
    board = state.get('board') or {}
    print('club:', club_id)
    for k in ('mode', 'combined', 'is_rx', 'split_count'):
        print(f'  {k}: {board.get(k)!r}')
    rows = board.get('rows') or []
    print('  rows:', len(rows))
    if rows:
        print('  row0 splits:', rows[0].get('splits'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
