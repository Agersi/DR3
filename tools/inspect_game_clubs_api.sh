#!/bin/bash
# Dump /api/game/clubs for a user's most recent active game token — exactly
# what the local client's dispatcher receives from the web. Prints each event's
# id, club, mode and full settings (verify custom flags like single_challenge
# survive normalize_championship), filtered to the given event ids when passed.
# Read-only.
# Run:  python tools/vps.py --run-script tools/inspect_game_clubs_api.sh [USERNAME] [EVENT_ID ...]
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
curl -s http://127.0.0.1:8001/api/game/clubs -H "Authorization: Bearer $TOKEN" > "$OUT"
/opt/dr3/.venv/bin/python - "$@" "$OUT" <<'EOF'
import json, sys
want, path = sys.argv[1:-1], sys.argv[-1]
data = json.load(open(path))
for ev in data.get('events', []):
    if want and ev.get('id') not in want:
        continue
    s = ev.get('settings') or {}
    print(f"event={ev.get('id')} club={ev.get('club_id')} mode={ev.get('mode')} "
          f"legs={len(ev.get('events', []))} single_challenge={s.get('single_challenge')}")
EOF
rm -f "$OUT"
