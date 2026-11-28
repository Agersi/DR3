#!/bin/bash
# S3: catch-up simulation — drop the off-site marker and re-run; every local file must be re-pushed.
set -euo pipefail
rm /var/backups/dr3/.last_offsite
/usr/local/bin/dr3-backup.sh
echo "marker now: $(cat /var/backups/dr3/.last_offsite)"

echo "=== recent app-cron ticks (root, every 5 min) ==="
journalctl -u cron --since '-2h' 2>/dev/null | grep -a 'dr3-cron' | tail -5 || true
grep -a 'CRON' /var/log/syslog 2>/dev/null | grep -a 'dr3-cron' | tail -5 || true
