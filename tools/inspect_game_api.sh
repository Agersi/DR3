#!/bin/bash
cd /opt/dr3/web
TOKEN=$(/opt/dr3/.venv/bin/python - <<'EOF'
import re, pymysql
env = {}
for line in open('.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k] = v
m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)', env['DATABASE_URL'])
conn = pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306), user=m.group(1), password=m.group(2), database=m.group(5).split('?')[0])
cur = conn.cursor()
cur.execute("SELECT token FROM game_tokens WHERE revoked_at IS NULL ORDER BY id DESC LIMIT 1")
print(cur.fetchone()[0])
EOF
)
echo "== GET /api/game/clubs (token redacted in header, response below) =="
curl -s http://127.0.0.1:8001/api/game/clubs -H "Authorization: Bearer $TOKEN" | /opt/dr3/.venv/bin/python -m json.tool
