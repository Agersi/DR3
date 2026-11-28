#!/bin/bash
# Dump dr3-web gunicorn access-log lines for /api/game/ from the journal,
# so stage-flow RPC ordering can be reconstructed server-side.
# Usage: inspect_api_log.sh [SINCE] [UNTIL]   (journalctl -S/-U values)
# Permanent tool for terminal-damage / stage-flow investigation (backlog #32).
SINCE="${1:-today}"
UNTIL="${2:-}"
if [ -n "$UNTIL" ]; then
  journalctl -u dr3-web.service --no-pager -S "$SINCE" -U "$UNTIL" | grep -E '/api/game/' | tail -n 300
else
  journalctl -u dr3-web.service --no-pager -S "$SINCE" | grep -E '/api/game/' | tail -n 300
fi
