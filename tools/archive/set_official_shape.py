#!/usr/bin/env python3
"""One-shot (ChallengeType=1 experiment, backlog #13): serve ONE test club in
the full upstream-official challenge shape so we can test in-game whether a
type=1 + Type=0 (single-vehicle) requirement renders on the clubs screen and
restricts car-select without crashing.

Sets on "Single Car Rally Test" (evt-d4c3af19), merging into existing settings:
    vehicle_ids=[575], challenge_type=1, scoring_type=0, challenge_mode=0,
    use_inv_vehicle=true
This mirrors the real EGO single-car officials (T0 + type=1/scoring=0/mode=0/
UseInvVehicle=true). The other two test clubs are left untouched (class-only,
working shape). Idempotent; prints before/after.

Run on the VPS: /opt/dr3/.venv/bin/python /tmp/set_official_shape.py
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

CLUB = "Single Car Rally Test"
OVERRIDE = {
    "vehicle_ids": [575],
    "challenge_type": 1,
    "scoring_type": 0,
    "challenge_mode": 0,
    "use_inv_vehicle": True,
}

cur = conn.cursor()
ok = True
cur.execute("SELECT id FROM clubs WHERE name = %s", (CLUB,))
club_ids = [r[0] for r in cur.fetchall()]
if len(club_ids) != 1:
    print(f"SKIP {CLUB!r}: {len(club_ids)} matching clubs")
    ok = False
else:
    cid = club_ids[0]
    cur.execute("SELECT id, name, settings_json FROM events WHERE club_id = %s", (cid,))
    rows = cur.fetchall()
    if len(rows) != 1:
        print(f"SKIP {CLUB!r}: {len(rows)} events for club {cid}")
        ok = False
    else:
        eid, ev_name, settings_json = rows[0]
        settings = json.loads(settings_json or "{}")
        before = {k: settings.get(k) for k in OVERRIDE}
        settings.update(OVERRIDE)
        cur.execute("UPDATE events SET settings_json = %s WHERE id = %s",
                    (json.dumps(settings), eid))
        print(f"OK club={CLUB!r} event={eid} ({ev_name!r})")
        for k in OVERRIDE:
            print(f"   {k}: {before.get(k)!r} -> {OVERRIDE[k]!r}")
conn.commit()

print("\nverify (all three test clubs):")
cur.execute("SELECT c.name, e.id, e.car_class, e.settings_json FROM events e "
            "JOIN clubs c ON c.id = e.club_id WHERE c.name IN (%s,%s,%s)",
            ("Single Car Rally Test", "Same Class Multi Car Rally Test",
             "Multi Car Rally Test"))
for cname, eid, car_class, sj in cur.fetchall():
    s = json.loads(sj or "{}")
    print(f"  {cname!r} event={eid} class={car_class!r} vehicle_ids="
          f"{s.get('vehicle_ids')} challenge_type={s.get('challenge_type')} "
          f"scoring_type={s.get('scoring_type')} challenge_mode="
          f"{s.get('challenge_mode')} use_inv_vehicle={s.get('use_inv_vehicle')}")
conn.close()
raise SystemExit(0 if ok else 1)
