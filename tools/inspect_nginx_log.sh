#!/bin/bash
# Dump /rally/ request lines from the nginx access log for a date + hour
# (VPS local time = UTC). Run with --sudo.
# Usage: inspect_nginx_log.sh DATE HOUR [EXTRA_GREP]
#   e.g. inspect_nginx_log.sh 28/Oct/2026 04 stage-complete
# Permanent tool for server-side stage-flow investigation (backlog #32).
DATE="${1:?DATE required, e.g. 28/Oct/2026}"
HOUR="${2:?HOUR required, e.g. 04}"
EXTRA="${3:-rally}"
grep -F "[$DATE:$HOUR:" /var/log/nginx/access.log | grep -E "$EXTRA" | tail -n 300
