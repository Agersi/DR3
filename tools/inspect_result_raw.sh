#!/bin/bash
# Print the raw stages_json / in_progress_json of one or more result rows.
# Usage: inspect_result_raw.sh RESULT_ID [RESULT_ID ...]
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
for rid in sys.argv[1:]:
    cur.execute("SELECT id, event_id, username, stages_json, in_progress_json FROM results WHERE id=%s", (str(rid),))
    row = cur.fetchone()
    if not row:
        print(f'result {rid}: not found')
        continue
    rid, eid, user_, sj, ipj = row
    print(f'== result {rid} user={user_} event={eid}')
    for s in json.loads(sj or '[]'):
        print(f'  stage: {json.dumps(s)}')
    print(f'  in_progress: {ipj}')
EOF
