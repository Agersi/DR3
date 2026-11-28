#!/bin/bash
# Print an event's per-leg stage layout (names + time limits) from the DB.
# Usage: inspect_event_stages.sh EVENT_ID [EVENT_ID ...]
# Permanent tool for stage-flow investigation (backlog #32).
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$@" <<'EOF'
import json, re, sys
env = {}
for line in open('.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k] = v
m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)', env['DATABASE_URL'])
user, pw, host, port, dbname = m.group(1), m.group(2), m.group(3), int(m.group(4) or 3306), m.group(5).split('?')[0]
import pymysql
conn = pymysql.connect(host=host, port=port, user=user, password=pw, database=dbname)
cur = conn.cursor()
for eid in sys.argv[1:]:
    cur.execute("SELECT id, name, schema_version, legs_json, stages_json, settings_json FROM events WHERE id=%s", (eid,))
    row = cur.fetchone()
    if not row:
        print(f'{eid}: not found')
        continue
    eid, name, sv, legs_json, stages_json, settings_json = row
    print(f'== {eid} {name!r} schema_v{sv}')
    settings = json.loads(settings_json or '{}')
    print(f'   mode={settings.get("mode")} scoring={settings.get("scoring_system")}')
    if sv == 2:
        for li, leg in enumerate(json.loads(legs_json or '[]')):
            print(f'   leg {li}: location={leg.get("location")!r} class={leg.get("car_class")!r}')
            for si, s in enumerate(leg.get('stages', []) or []):
                keys = {k: v for k, v in s.items() if k not in ('fields',)}
                print(f'      stage {si}: {json.dumps(s)[:400]}')
    else:
        for si, s in enumerate(json.loads(stages_json or '[]')):
            print(f'      stage {si}: {json.dumps(s)[:400]}')
EOF
