#!/bin/bash
# Baseline HTTP health check (see docs/notes/vps_server_state.md "Baseline HTTP health").
# Port 80 301-redirects to HTTPS, so probe https://byagersi.com with -k (self-check on the box).
for p in / /rollerbot /phpmyadmin /server/ /download_rb/ /rally/; do
  printf '%s -> ' "$p"
  curl -sk -o /dev/null -w '%{http_code}\n' "https://byagersi.com$p"
done
