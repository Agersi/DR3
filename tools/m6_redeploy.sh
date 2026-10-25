#!/bin/bash
set -e
tar -xzf /tmp/web.tar.gz -C /opt/dr3
chown -R agersi:agersi /opt/dr3/web
rm -f /tmp/web.tar.gz
systemctl restart dr3-web.service
sleep 4
echo "active: $(systemctl is-active dr3-web.service)"
ss -ltn | grep 8001 || echo PORT_8001_NOT_LISTENING
journalctl -u dr3-web.service --no-pager -n 6
