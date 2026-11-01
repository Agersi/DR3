#!/bin/bash
# Diagnostics: what is waiting for locks / how are the web workers doing.
set -e
echo '--- gunicorn:'
systemctl status dr3-web --no-pager | head -15
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
import os
from urllib.parse import urlparse
import pymysql

u = urlparse(os.environ['DATABASE_URL'])
conn = pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username,
                       password=u.password, database=u.path.lstrip('/'))
cur = conn.cursor()
print('--- mysql processlist:')
cur.execute("SELECT id, user, host, db, command, time, state, LEFT(info,100) "
            "FROM information_schema.processlist ORDER BY time DESC")
for row in cur.fetchall():
    print('  ', row)
print('--- active innodb transactions:')
cur.execute("SELECT trx_id, trx_mysql_thread_id, trx_started, trx_state, "
            "LEFT(trx_query,100) FROM information_schema.innodb_trx")
for row in cur.fetchall():
    print('  ', row)
print('--- metadata locks (mysql 8):')
try:
    cur.execute("SELECT * FROM performance_schema.metadata_locks")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        print('  ', dict(zip(cols, row)))
except Exception as e:
    print('  (unavailable:', e, ')')
conn.close()
EOF
