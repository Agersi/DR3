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

print('== users ==')
cur.execute("SELECT id, username, email, email_verified FROM users")
for r in cur.fetchall():
    print(r)

print('== game_tokens (truncated) ==')
cur.execute("SELECT id, user_id, LEFT(token, 8), revoked_at, created_at FROM game_tokens")
for r in cur.fetchall():
    print(r)

print('== clubs ==')
cur.execute("SELECT * FROM clubs")
cols = [d[0] for d in cur.description]
for r in cur.fetchall():
    print(dict(zip(cols, r)))

print('== club_members ==')
cur.execute("SELECT * FROM club_members")
cols = [d[0] for d in cur.description]
for r in cur.fetchall():
    print(dict(zip(cols, r)))

print('== events ==')
cur.execute("SELECT id, name, type, club_id, start_time, end_time, active, featured, system FROM events")
cols = [d[0] for d in cur.description]
for r in cur.fetchall():
    print(dict(zip(cols, r)))

print('== championships (v2 legs summary) ==')
cur.execute("SELECT id, name, JSON_LENGTH(legs_json) FROM events WHERE schema_version=2")
for r in cur.fetchall():
    print(r)
EOF
