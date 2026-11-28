#!/bin/bash
# S2 (Phase 3, 2026-11-28): dedicated sudo-less 'dr3' user for the gunicorn service.
# Additive: creates the user if absent, re-owns /opt/dr3 to dr3, switches
# dr3-web.service to User=dr3. web/.env is dr3-only (S3 done 2026-11-28):
# cron runs as root and reads CRON_API_KEY itself.
# Deploy scripts keep running as root via agersi sudo (root bypasses ownership).
set -e
id dr3 >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin dr3
# gunicorn 26's control socket lives in $HOME/.gunicorn — a system account has
# no home, so create one (dr3-owned) or worker boot logs PermissionError.
mkdir -p /home/dr3
chown dr3:dr3 /home/dr3
chmod 750 /home/dr3
chown -R dr3:dr3 /opt/dr3
chmod 755 /opt/dr3
chown dr3:dr3 /opt/dr3/web/.env
chmod 600 /opt/dr3/web/.env
sed -i 's/^User=.*/User=dr3/; s/^Group=.*/Group=dr3/' /etc/systemd/system/dr3-web.service
systemctl daemon-reload
systemctl restart dr3-web.service
sleep 4
echo "active: $(systemctl is-active dr3-web.service)"
ss -ltnp | grep 8001 || echo PORT_8001_NOT_LISTENING
su -s /bin/sh agersi -c 'grep -m1 -q ^CRON_API_KEY= /opt/dr3/web/.env && echo CRON_ENV_READABLE || echo CRON_ENV_BLOCKED' # post-S3 expect CRON_ENV_BLOCKED
journalctl -u dr3-web.service --no-pager -n 8
