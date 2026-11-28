#!/bin/bash
# Dump a full events-table row (all columns) for one or more event ids.
# Usage: inspect_event_full.sh EVENT_ID [EVENT_ID ...]
# Permanent tool for stage-flow investigation (backlog #32).
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$@" <<'EOF'
import re, sys
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
    cur.execute("SELECT * FROM events WHERE id=%s", (eid,))
    row = cur.fetchone()
    if not row:
        print(f'{eid}: not found')
        continue
    cols = [d[0] for d in cur.description]
    print(f'== {eid}')
    for c, v in zip(cols, row):
        s = str(v)
        if len(s) > 600:
            s = s[:600] + f'... [{len(str(v))} chars]'
        print(f'   {c}: {s}')
EOF
