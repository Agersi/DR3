#!/bin/bash
# Dump dr3-web journal lines for a time window, optionally filtered by a
# pattern. Run with --sudo (journal needs root).
# Usage: inspect_journal.sh DATE SINCE_TIME UNTIL_TIME [GREP_PATTERN]
#   e.g. inspect_journal.sh 2026-10-28 04:10 04:25 '/api/game/'
# (vps.py run-script forwards args unquoted, so no argument may contain a
# space — date and times are separate words.)
# Permanent tool for server-side stage-flow investigation (backlog #32).
DATE="${1:?DATE required}"
SINCE_T="${2:?SINCE_TIME required}"
UNTIL_T="${3:?UNTIL_TIME required}"
PAT="${4:-/api/game/}"
journalctl -u dr3-web.service --no-pager -q -S "$DATE $SINCE_T" -U "$DATE $UNTIL_T" | grep -E "$PAT" | tail -n 400
