#!/bin/bash
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - <<'EOF'
import json, re
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
cur.execute("SELECT id, club_id, data_json FROM championship_drafts")
for row in cur.fetchall():
    print('=== draft', row[0], 'club', row[1])
    print(json.dumps(json.loads(row[2]), indent=1)[:4000])
EOF
