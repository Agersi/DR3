#!/bin/bash
# Create a test club + championship mirroring an existing event, on the VPS DB.
#
# Mirrors the source event's legs/stages/settings/class exactly (same stage
# lists, conditions, service areas) so in-game behavior — including
# terminal-damage close-out flows — is reproduced 1:1. The new club carries
# the test name, which becomes the in-game challenge name (events display the
# club name).
#
# Usage (via tools/vps.py --run-script; club-name words come LAST and are
# re-joined here because run_script forwards args unquoted):
#   python tools/vps.py --run-script tools/create_test_championship.sh \
#       SOURCE_EVENT OWNER DAYS CLUB_NAME_WORD1 [CLUB_NAME_WORD2 ...]
# Example:
#   ... create_test_championship.sh evt-10872bf1 Agersi 3 Terminal Testing
#
# Permanent tool (backlog #32 test-event setup); parameterized via argv.
src="$1"; owner="$2"; days="$3"; shift 3
club_name="$*"
if [ -z "$src" ] || [ -z "$owner" ] || [ -z "$days" ] || [ -z "$club_name" ]; then
    echo "usage: create_test_championship.sh SOURCE_EVENT OWNER DAYS CLUB_NAME_WORDS..." >&2
    exit 2
fi
/opt/dr3/.venv/bin/python - "$src" "$owner" "$days" "$club_name" <<'EOF'
import json, re, sys, uuid
from datetime import datetime, timedelta

env = {}
for line in open('/opt/dr3/web/.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k] = v
m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)',
             env['DATABASE_URL'])
import pymysql
conn = pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306),
                       user=m.group(1), password=m.group(2),
                       database=m.group(5).split('?')[0])
cur = conn.cursor()

src_event, owner, days, club_name = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]

cur.execute("SELECT id FROM users WHERE username=%s", (owner,))
row = cur.fetchone()
if not row:
    raise SystemExit(f'owner {owner!r} not found in users')
owner_id = row[0]

cur.execute(
    "SELECT schema_version, name, type, location, car_class, surface,"
    " conditions, stages_json, settings_json, legs_json"
    " FROM events WHERE id=%s", (src_event,))
s = cur.fetchone()
if not s:
    raise SystemExit(f'source event {src_event!r} not found')
(schema_version, _name, etype, location, car_class, surface,
 conditions, stages_json, settings_json, legs_json) = s

club_id = 'club-' + uuid.uuid4().hex[:8]
event_id = 'evt-' + uuid.uuid4().hex[:8]
now = datetime.utcnow()
start = now - timedelta(hours=1)
end = now + timedelta(days=days)

cur.execute(
    "INSERT INTO clubs (id, name, description, visibility, join_policy,"
    " created_by, admins_json, pending_requests_json, invites_json,"
    " cooldowns_json) VALUES (%s,%s,%s,'public','open',%s,%s,'[]','[]','{}')",
    (club_id, club_name, 'Test club - terminal damage investigation',
     owner_id, json.dumps([owner])))
cur.execute(
    "INSERT INTO club_members (club_id, user_id, role) VALUES (%s,%s,'admin')",
    (club_id, owner_id))
cur.execute(
    "INSERT INTO events (id, schema_version, name, type, location,"
    " car_class, surface, conditions, stages_json, settings_json,"
    " legs_json, start_time, end_time, active, featured, club_id,"
    " system, slot_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
    " 1,0,%s,0,NULL)",
    (event_id, schema_version, club_name, etype, location, car_class,
     surface, conditions, stages_json, settings_json, legs_json, start,
     end, club_id))
conn.commit()

print(f'club:  {club_id} ({club_name!r}, owner={owner})')
print(f'event: {event_id} (mirrors {src_event})')
print(f'window: {start.isoformat()} .. {end.isoformat()} UTC')
settings = json.loads(settings_json)
print(f'settings: mode={settings.get("mode")} scoring={settings.get("scoring")}'
      f' power_stage={settings.get("power_stage")}')
for i, leg in enumerate(json.loads(legs_json)):
    print(f'  leg {i}: {leg.get("location")} / {leg.get("car_class")}'
          f' / {len(leg.get("stages", []))} stages')
EOF
