#!/usr/bin/env python3
"""Print the overall-board rows (driver + tyre sequence) for one event,
exactly as event_board builds them. Debug helper for the multi-location
tyre-sequence issue.

Run on the VPS:
    /opt/dr3/.venv/bin/python inspect_board_rows.py EVENT_ID [ROUND] [MODE]
"""
import os
import sys
from datetime import datetime, timezone

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, "/opt/dr3/web")

from app import create_app  # noqa: E402
from app.events import get_event  # noqa: E402
from app.results_view import event_board  # noqa: E402

event_id = sys.argv[1] if len(sys.argv) > 1 else None
round_index = int(sys.argv[2]) if len(sys.argv) > 2 else 0
mode = sys.argv[3] if len(sys.argv) > 3 else 'overall'
if not event_id:
    sys.exit("usage: inspect_board_rows.py EVENT_ID [ROUND] [MODE]")

app = create_app()
with app.app_context():
    event = get_event(event_id)
    if not event:
        sys.exit(f"unknown event: {event_id}")
    # Stage index 999 clamps to the last stage, so Overall spans the whole
    # event (multi-location) or the whole round (season). The request context
    # lets url_for build flag/static URLs outside a live request.
    with app.test_request_context('/'):
        board = event_board(event, round_index, mode, 999,
                            now=datetime.now(timezone.utc).replace(tzinfo=None))
    if not board:
        sys.exit("board is None")
    print(f"combined={board.get('combined')} stages={len(board.get('stages_bar', []))}")
    for r in board.get('rows', []):
        print(f"  {r['username']}: tyres={r.get('tyres')} "
              f"spares={r.get('spares')} dnf={r.get('dnf')}")
