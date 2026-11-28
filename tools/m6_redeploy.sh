#!/bin/bash
set -e
tar -xzf /tmp/web.tar.gz -C /opt/dr3
chown -R dr3:dr3 /opt/dr3
# .env stays group-readable by agersi: the existing cron (runs as agersi)
# reads CRON_API_KEY from it (cron hardening is S3, not done yet).
chown dr3:agersi /opt/dr3/web/.env
chmod 640 /opt/dr3/web/.env
rm -f /tmp/web.tar.gz
systemctl restart dr3-web.service
sleep 4
echo "active: $(systemctl is-active dr3-web.service)"
ss -ltn | grep 8001 || echo PORT_8001_NOT_LISTENING
journalctl -u dr3-web.service --no-pager -n 6
