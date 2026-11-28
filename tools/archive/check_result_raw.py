#!/usr/bin/env python3
"""Dump the full raw result stage entries + stage_runs rows for one user/event.

Used to determine actual stage play order (multi-mode free order) and inspect
every stored field per stage, including timestamps on the run-detail rows.

Run on the VPS:
    /opt/dr3/.venv/bin/python check_result_raw.py EVENT_ID USERNAME
"""
import json
import os
import re
import sys

if len(sys.argv) < 3:
    sys.exit("usage: check_result_raw.py EVENT_ID USERNAME")
event_id, username = sys.argv[1], sys.argv[2]

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import pymysql  # noqa: E402

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             os.environ["DATABASE_URL"])
conn = pymysql.connect(host=p.group(3), port=int(p.group(4) or 3306),
                       user=p.group(1), password=p.group(2),
                       database=p.group(5).split("?")[0])
cur = conn.cursor()

cur.execute("SELECT stages_json FROM club_results WHERE event_id=%s"
            " AND username=%s", (event_id, username))
row = cur.fetchone()
if not row:
    sys.exit("no result row")
print("=== raw stages_json ===")
for i, s in enumerate(json.loads(row[0])):
    print(f"stage {i}: {json.dumps(s)}")

cur.execute("SHOW COLUMNS FROM stage_runs")
cols = [r[0] for r in cur.fetchall()]
print("\n=== stage_runs columns ===")
print(cols)
cur.execute("SELECT * FROM stage_runs WHERE event_id=%s AND username=%s"
            " ORDER BY id", (event_id, username))
for r in cur.fetchall():
    print("run:", dict(zip(cols, r)))
