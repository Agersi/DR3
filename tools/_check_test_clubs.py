#!/usr/bin/env python3
"""Read-only: show the top-level car_class column + settings for the three
#13 test clubs, to confirm what shape their served challenge will take after
vehicle_ids/use_inv_vehicle were cleared (tools/cleanup_vehicle_reqs.py).

Run on the VPS: /opt/dr3/.venv/bin/python /tmp/_check_test_clubs.py
"""
import json
import os
from urllib.parse import unquote, urlparse

ENV_PATH = "/opt/dr3/web/.env"
with open(ENV_PATH) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import pymysql  # noqa: E402

p = urlparse(os.environ["DATABASE_URL"])
conn = pymysql.connect(
    host=p.hostname or "127.0.0.1", port=p.port or 3306,
    user=unquote(p.username or ""), password=unquote(p.password or ""),
    database=p.path.lstrip("/") or None,
)
CLUBS = ["Single Car Rally Test", "Same Class Multi Car Rally Test",
         "Multi Car Rally Test"]
cur = conn.cursor()
cur.execute("SELECT c.name, e.id, e.car_class, e.settings_json FROM events e "
            "JOIN clubs c ON c.id = e.club_id WHERE c.name IN (%s,%s,%s)",
            tuple(CLUBS))
for cname, eid, car_class, sj in cur.fetchall():
    s = json.loads(sj or "{}")
    print(f"{cname!r}\n  event={eid} car_class={car_class!r} "
          f"vehicle_ids={s.get('vehicle_ids')} use_inv_vehicle="
          f"{s.get('use_inv_vehicle')}")
conn.close()
