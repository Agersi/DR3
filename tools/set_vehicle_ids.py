#!/usr/bin/env python3
"""One-shot (#13 probe): inject settings.vehicle_ids into the three test
championships, resolved by CLUB name (event display names carry the club
name).  Idempotent; prints before/after; refuses ambiguous matches.

Run on the VPS: /opt/dr3/.venv/bin/python /tmp/set_vehicle_ids.py
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

# club name -> vehicle_ids to enforce (Vehicle enum ids, dr3/dr2server/game_data.py)
PLAN = {
    "Single Car Rally Test": [575],            # BMW M1 Procar Rally only
    "Same Class Multi Car Rally Test": [575, 480],   # + Lancia 037 Evo 2 (Group B RWD)
    "Multi Car Rally Test": [575, 536],        # + Mitsubishi Lancer Evo VI (Group A)
}

cur = conn.cursor()
ok = True
for club_name, vids in PLAN.items():
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
    old = settings.get("vehicle_ids")
    settings["vehicle_ids"] = vids
    cur.execute("UPDATE events SET settings_json = %s WHERE id = %s",
                (json.dumps(settings), eid))
    print(f"OK   club={club_name!r} event={eid} ({ev_name!r}) "
          f"vehicle_ids: {old} -> {vids}")
conn.commit()

print("\nverify:")
cur.execute("SELECT c.name, e.id, e.settings_json FROM events e "
            "JOIN clubs c ON c.id = e.club_id WHERE c.name IN (%s,%s,%s)",
            tuple(PLAN))
for cname, eid, sj in cur.fetchall():
    print(f"  {cname!r} event={eid} vehicle_ids={json.loads(sj).get('vehicle_ids')}")
conn.close()
raise SystemExit(0 if ok else 1)
