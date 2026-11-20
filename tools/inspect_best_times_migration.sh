#!/bin/bash
# Reports the state of the best_times tyre-compound migration on the live DB:
# column presence, unique-key columns, and zero-compound row resolvability.
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - <<'EOF'
import re
env = {}
for line in open('.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k] = v
m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)', env['DATABASE_URL'])
import pymysql
conn = pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306),
                       user=m.group(1), password=m.group(2), database=m.group(5).split('?')[0])
cur = conn.cursor()

print('== columns ==')
cur.execute("SHOW COLUMNS FROM best_times")
for r in cur.fetchall():
    print(f'{r[0]} {r[1]} {"NULL" if r[2] == "YES" else "NOT NULL"}')

print('== uq_best_times_key ==')
cur.execute("SHOW INDEX FROM best_times WHERE Key_name='uq_best_times_key'")
for r in cur.fetchall():
    print('col', r[3], r[5])

print('== zero-compound rows ==')
cur.execute("SELECT COUNT(*) FROM best_times")
total = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM best_times WHERE tyre_compound=0")
zeros = cur.fetchone()[0]
print(f'total={total} compound_0={zeros}')

print('== zero rows with a resolvable stage_runs match (should be 0) ==')
cur.execute('''
    SELECT COUNT(*) FROM best_times b
    WHERE b.tyre_compound=0
      AND EXISTS (SELECT 1 FROM stage_runs s
                  WHERE s.username=b.username AND s.vehicle_id=b.vehicle_id
                    AND s.track_model_id=CAST(b.track AS UNSIGNED)
                    AND s.conditions_id=CAST(b.conditions AS UNSIGNED)
                    AND s.tyre_compound>0)
''')
print('resolvable_still_zero =', cur.fetchone()[0])
EOF
