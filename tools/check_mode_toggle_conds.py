"""Evaluate each sub-condition of the render_check mode-toggle check (diagnostic).

Usage: python tools/check_mode_toggle_conds.py [club_id]
Reuses web/smoke.db like render_check.py — run smoke_test.py first.
Prints only the failing conditions.
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
    det = client.get(f'/clubs/{club_id}').get_data(as_text=True)
    m = re.search(r'id="board-state">(.*?)</script>', det, re.S)
    bs = json.loads(m.group(1)) if m else {}

    conds = {
        'stage>0': det.find('data-mode="stage"') > -1,
        'stage<overall': det.find('data-mode="stage"') < det.find('data-mode="overall"'),
        'mode==stage': bs.get('mode') == 'stage',
        'no VR th': '<th>VR</th>' not in det,
        'no Assists th': '<th>Assists</th>' not in det,
        'no colspan13': 'colspan="13"' not in det,
        'inputCell call': "inputCell(r.input, r.vr, r.assists)" in det,
        'INPUT_ICON': "INPUT_ICON = { wheel: 'wheel.png', controller: 'controller.png', keyboard: 'keyboard.png' };" in det,
        'vr push': "if (vr === true) {\n      parts.push('<img class=\"input-icon\" src=\"' + STATIC + 'icons/vr.png\"" in det,
        'assist push': "if (assists === true) {\n      parts.push('<img class=\"input-icon\" src=\"' + STATIC + 'icons/assist.png\"" in det,
        'split_count int': isinstance((bs.get('board') or {}).get('split_count'), int),
        'is_rx key': 'is_rx' in (bs.get('board') or {}),
    }
    bad = {k: v for k, v in conds.items() if not v}
    print('failing:', json.dumps(bad) if bad else 'none')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
