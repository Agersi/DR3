#!/bin/bash
echo "=== /home/agersi/apps ==="
ls -la /home/agersi/apps/ 2>/dev/null
find /home/agersi/apps -maxdepth 2 -name "package.json" 2>/dev/null | head
echo "=== pm2 describe download-rb ==="
pm2 describe download-rb 2>/dev/null | grep -E "script path|exec cwd|interpreter|created at|unstable restarts" 
echo "=== pm2 describe garage-dev ==="
pm2 describe garage-dev 2>/dev/null | grep -E "script path|exec cwd|interpreter|created at|unstable restarts"
echo "=== baseline HTTP checks (from server) ==="
for u in "/" "/rollerbot" "/phpmyadmin" "/server/" "/download_rb/"; do
  code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 10 "https://byagersi.com$u")
  echo "$u -> $code"
done
echo "=== python venv test ==="
python3 -m venv /tmp/venvtest 2>&1 && echo "venv OK" || echo "venv FAILED (need python3-venv pkg)"
rm -rf /tmp/venvtest
echo "=== apt packages: python3-venv / uv ==="
dpkg -l | grep -E "python3.12-(venv|dev)|^ii\s+uv" || echo "none of python3-venv/python3-dev/uv installed"
echo "=== disk inode + /var/www size ==="
du -sh /var/www/byagersi.com 2>/dev/null
df -i / | tail -1
echo "=== nginx log dir ==="
ls -la /var/log/nginx/ | head
echo "=== existing systemd units mentioning dr3 or rally ==="
systemctl list-unit-files --no-pager 2>/dev/null | grep -iE "dr3|rally|gunicorn" || echo "none (clean slate for DR3)"
