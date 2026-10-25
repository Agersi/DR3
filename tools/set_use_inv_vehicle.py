#!/usr/bin/env python3
"""One-shot (#13 crash isolation): set settings.use_inv_vehicle=False on the
three vehicle_ids test championships (resolved by CLUB name), so the served
challenges carry Type=0 requirements WITHOUT UseInvVehicle=true — the shape
real EGO club challenges use.  Idempotent; prints before/after.

Run on the VPS: /opt/dr3/.venv/bin/python /tmp/set_use_inv_vehicle.py
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
    host=p.hostname or "127.0.0.1",
    port=p.port or 3306,
    user=unquote(p.username or ""),
    password=unquote(p.password or ""),
    database=p.path.lstrip("/") or None,
)

CLUBS = [
    "Single Car Rally Test",
    "Same Class Multi Car Rally Test",
    "Multi Car Rally Test",
]

cur = conn.cursor()
ok = True
for club_name in CLUBS:
    cur.execute("SELECT id FROM clubs WHERE name = %s", (club_name,))
    club_ids = [r[0] for r in cur.fetchall()]
    if len(club_ids) != 1:
        print(f"SKIP {club_name!r}: {len(club_ids)} matching clubs")
        ok = False
        continue
    cid = club_ids[0]
    cur.execute("SELECT id, name, settings_json FROM events WHERE club_id = %s", (cid,))
    rows = cur.fetchall()
    if len(rows) != 1:
        print(f"SKIP {club_name!r}: {len(rows)} events for club {cid}: "
              f"{[(r[0], r[1]) for r in rows]}")
        ok = False
        continue
    eid, ev_name, settings_json = rows[0]
    settings = json.loads(settings_json or "{}")
    old = settings.get("use_inv_vehicle")
    settings["use_inv_vehicle"] = False
    cur.execute("UPDATE events SET settings_json = %s WHERE id = %s",
                (json.dumps(settings), eid))
    print(f"OK   club={club_name!r} event={eid} ({ev_name!r}) "
          f"use_inv_vehicle: {old} -> False")
conn.commit()

print("\nverify:")
cur.execute("SELECT c.name, e.id, e.settings_json FROM events e "
            "JOIN clubs c ON c.id = e.club_id WHERE c.name IN (%s,%s,%s)",
            tuple(CLUBS))
for cname, eid, sj in cur.fetchall():
    s = json.loads(sj)
    print(f"  {cname!r} event={eid} vehicle_ids={s.get('vehicle_ids')} "
          f"use_inv_vehicle={s.get('use_inv_vehicle')}")
conn.close()
raise SystemExit(0 if ok else 1)
