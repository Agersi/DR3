#!/bin/bash
# Dump [STAGE]/[DATA]/[CHAMP] dispatcher log lines from the dr3-web journal.
# Usage: inspect_stage_log.sh [SINCE] [UNTIL]   (journalctl -S/-U values, e.g. "today 04:00")
# Permanent tool for terminal-damage / stage-flow investigation (backlog #32).
SINCE="${1:-today}"
UNTIL="${2:-}"
if [ -n "$UNTIL" ]; then
  journalctl -u dr3-web.service --no-pager -S "$SINCE" -U "$UNTIL" | grep -E '\[(STAGE|DATA|CHAMP|LB|PROG)\]' | tail -n 200
else
  journalctl -u dr3-web.service --no-pager -S "$SINCE" | grep -E '\[(STAGE|DATA|CHAMP|LB|PROG)\]' | tail -n 200
fi
