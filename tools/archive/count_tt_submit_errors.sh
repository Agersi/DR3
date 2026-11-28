#!/bin/bash
# Read-only: count today's time-trial-submit responses by HTTP status, and list the 500s.
set -e
echo "=== status-code histogram (today's access.log) ==="
grep -oE 'time-trial-submit HTTP/1\.1" [0-9]+' /var/log/nginx/access.log \
  | sed -E 's/.*" ([0-9]+)/\1/' | sort | uniq -c
echo "=== the 500 lines (today) ==="
grep -E 'time-trial-submit HTTP/1\.1" 500' /var/log/nginx/access.log || echo "(none)"
