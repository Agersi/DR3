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
echo "== clubs+events =="
curl -s http://127.0.0.1:8001/api/game/clubs -H "Authorization: Bearer $TOKEN" | /opt/dr3/.venv/bin/python -c '
import json,sys
d = json.load(sys.stdin)
print("ok:", d.get("ok"))
for c in d.get("clubs", []):
    print("CLUB", c.get("id"), c.get("name"))
for e in d.get("events", []):
    print("EVENT", e.get("id"), "active=", e.get("active"), "club_id=", e.get("club_id"), "start=", e.get("start_time"), "end=", e.get("end_time"))
'
echo "== leaderboard evt-60454014 =="
curl -s http://127.0.0.1:8001/api/game/leaderboard/evt-60454014 -H "Authorization: Bearer $TOKEN" | /opt/dr3/.venv/bin/python -m json.tool
