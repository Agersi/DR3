#!/bin/bash
# Dump /api/game/my-progress for a user's most recent active game token,
# filtered to the given event ids (all events when no id is passed). Read-only.
# Used to verify what the client's Progress builders will carry over into the
# next leg (completed_stages vehicle/livery data).
# Run:  python tools/vps.py --run-script tools/inspect_my_progress.sh [USERNAME] [EVENT_ID ...]
cd /opt/dr3/web
USER_FILTER="${1:-}"
if [ -n "$USER_FILTER" ]; then shift; fi
TOKEN=$(/opt/dr3/.venv/bin/python - "$USER_FILTER" <<'EOF'
import re, sys, pymysql
env = {}
for line in open('.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k] = v
m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)', env['DATABASE_URL'])
conn = pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306), user=m.group(1), password=m.group(2), database=m.group(5).split('?')[0])
cur = conn.cursor()
if sys.argv[1]:
    cur.execute("""SELECT t.token FROM game_tokens t
                   JOIN users u ON u.id = t.user_id
                   WHERE t.revoked_at IS NULL AND u.username=%s
                   ORDER BY t.id DESC LIMIT 1""", (sys.argv[1],))
else:
    cur.execute("SELECT token FROM game_tokens WHERE revoked_at IS NULL ORDER BY id DESC LIMIT 1")
row = cur.fetchone()
print(row[0] if row else "")
EOF
)
if [ -z "$TOKEN" ]; then
    echo "no active game token found${USER_FILTER:+ for user $USER_FILTER}"
    exit 1
fi
OUT=$(mktemp)
curl -s http://127.0.0.1:8001/api/game/my-progress -H "Authorization: Bearer $TOKEN" > "$OUT"
/opt/dr3/.venv/bin/python - "$@" "$OUT" <<'EOF'
import json, sys
want, path = sys.argv[1:-1], sys.argv[-1]
data = json.load(open(path))
for ev in data.get('events', []):
    if want and ev.get('event_id') not in want:
        continue
    print(json.dumps(ev, indent=2))
EOF
rm -f "$OUT"
