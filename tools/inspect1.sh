#!/bin/bash
echo "=== MARIADB: databases ==="
mysql -e "SHOW DATABASES;" 2>&1
echo "=== MARIADB: users (as root) ==="
mysql -uroot -e "SELECT user,host FROM mysql.user;" 2>&1
echo "=== MARIADB: root auth check done ==="
echo "=== /var/www/byagersi.com/pages ==="
ls /var/www/byagersi.com/pages 2>/dev/null | head -40
echo "=== /opt ==="
ls -la /opt/
echo "=== crontab (user) ==="
crontab -l 2>&1
echo "=== /etc/crontab + cron.d ==="
cat /etc/crontab 2>/dev/null
ls /etc/cron.d/ 2>/dev/null
for f in /etc/cron.d/*; do echo "--- $f"; cat "$f"; done 2>/dev/null
echo "=== systemd timers ==="
systemctl list-timers --all --no-pager 2>&1 | head -20
echo "=== python3 / uv / pip ==="
python3 --version 2>&1; which python3 pip3 uv 2>&1
echo "=== node/npm ==="
node --version 2>&1; npm --version 2>&1; which node npm pm2 2>&1
echo "=== letsencrypt certs ==="
ls /etc/letsencrypt/live/ 2>/dev/null
systemctl list-timers --no-pager 2>/dev/null | grep -i certbot
certbot certificates 2>/dev/null | head -20
echo "=== ufw ==="
ufw status verbose 2>&1 | head -15
echo "=== home dir ==="
ls -la /home/agersi 2>/dev/null | head -40
echo "=== port 8001 check ==="
ss -ltn | grep 8001 || echo "port 8001 FREE"
