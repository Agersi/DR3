#!/bin/bash
set -e
cat > /etc/systemd/system/dr3-web.service <<'UNIT'
[Unit]
Description=DR3 web backend (Flask/gunicorn)
After=network.target mariadb.service

[Service]
User=agersi
Group=agersi
WorkingDirectory=/opt/dr3/web
ExecStart=/opt/dr3/.venv/bin/gunicorn --bind 127.0.0.1:8001 --workers 2 "app:create_app()"
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable dr3-web.service
systemctl restart dr3-web.service
sleep 3
echo "active: $(systemctl is-active dr3-web.service)"
ss -ltn | grep 8001 || echo PORT_8001_NOT_LISTENING
journalctl -u dr3-web.service --no-pager -n 12
