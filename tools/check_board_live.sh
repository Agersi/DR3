#!/usr/bin/env bash
# Read-only: verify the deployed club-detail events board (combined stage view).
# Usage: python tools/vps.py --run-script tools/check_board_live.sh [CLUB_ID]
set -u
CLUB="${1:-club-35b1b82e}"
PAGE="http://127.0.0.1:8001/clubs/$CLUB"

echo "== GET $PAGE =="
CODE=$(curl -s -o /tmp/board_page.html -w '%{http_code}' "$PAGE")
echo "status=$CODE bytes=$(wc -c < /tmp/board_page.html)"

echo "mode-toggle occurrences (want 0): $(grep -c 'mode-toggle' /tmp/board_page.html || true)"
echo "board-state blob: $(grep -c 'id="board-state"' /tmp/board_page.html || true)"
echo "stage-bar present: $(grep -c 'id="stage-bar"' /tmp/board_page.html || true)"
echo "Overall header: $(grep -c '<th class="num">Overall</th>' /tmp/board_page.html || true)"
echo "old Tyre header (want 0): $(grep -c '>Tyre<' /tmp/board_page.html || true)"
echo "colspan=12 loading row: $(grep -c 'colspan="12"' /tmp/board_page.html || true)"
