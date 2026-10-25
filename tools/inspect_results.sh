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

print('== clubs ==')
cur.execute("SELECT id, name, created_by FROM clubs")
for r in cur.fetchall():
    print(r)

print('\n== events (all) ==')
cur.execute("""SELECT id, name, type, club_id, start_time, end_time, active, schema_version
               FROM events ORDER BY id""")
for r in cur.fetchall():
    print(r)

print('\n== event details for club "Just a test" ==')
cur.execute("SELECT id FROM clubs WHERE name LIKE %s", ('%test%',))
club_ids = [r[0] for r in cur.fetchall()]
print('matching club ids:', club_ids)
for cid in club_ids:
    cur.execute("SELECT * FROM events WHERE club_id=%s", (cid,))
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        ev = dict(zip(cols, row))
        eid = ev['id']
        print(f"\n--- event {eid} name={ev['name']!r} active={ev['active']} "
              f"start={ev['start_time']} end={ev['end_time']}")
        for jk in ('settings_json', 'legs_json', 'stages_json'):
            raw = ev.get(jk)
            try:
                parsed = json.loads(raw or '{}')
            except Exception:
                parsed = raw
            print(f"  {jk}: {json.dumps(parsed)[:3000]}")

print('\n== results for those events ==')
for cid in club_ids:
    cur.execute("SELECT id FROM events WHERE club_id=%s", (cid,))
    eids = [r[0] for r in cur.fetchall()]
    for eid in eids:
        print(f"\n--- results event={eid} ---")
        cur.execute("""SELECT id, username, car, vehicle_id, total_time_ms, attempts_used,
                              stages_json, in_progress_json FROM results
                       WHERE event_id=%s ORDER BY id""", (eid,))
        cols = [d[0] for d in cur.description]
        for row in cur.fetchall():
            r = dict(zip(cols, row))
            print(json.dumps(r, default=str)[:4000])
EOF
