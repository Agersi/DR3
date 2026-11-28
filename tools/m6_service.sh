#!/bin/bash
set -e
cat > /etc/systemd/system/dr3-web.service <<'UNIT'
[Unit]
Description=DR3 web backend (Flask/gunicorn)
After=network.target mariadb.service

[Service]
User=dr3
Group=dr3
WorkingDirectory=/opt/dr3/web
ExecStart=/opt/dr3/.venv/bin/gunicorn --bind 127.0.0.1:8001 --workers 2 "app:create_app()"
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
# gunicorn 26's control socket lives in $HOME/.gunicorn — a system account
# has no home, so create one (dr3-owned) or worker boot logs PermissionError.
mkdir -p /home/dr3
chown dr3:dr3 /home/dr3
chmod 750 /home/dr3
systemctl daemon-reload
systemctl enable dr3-web.service
systemctl restart dr3-web.service
sleep 3
echo "active: $(systemctl is-active dr3-web.service)"
ss -ltn | grep 8001 || echo PORT_8001_NOT_LISTENING
journalctl -u dr3-web.service --no-pager -n 12
