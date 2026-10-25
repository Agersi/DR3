#!/usr/bin/env python3
"""One-shot (#13 closed): clear ALL #13 experiment keys from the three
vehicle-restriction test championships (resolved by CLUB name) —
settings.vehicle_ids, settings.use_inv_vehicle, settings.challenge_type,
settings.scoring_type and settings.challenge_mode — so their served challenges
fall back to the canonical club shape: ChallengeType=2 / ScoringType=2 /
Mode=1 / Type=1 class requirement / UseInvVehicle=false (the shape that works).
Type=0 requirements crash club challenges regardless of ChallengeType (see
docs/PLAN.md item #13 final conclusion), so none of these keys may remain set.
Idempotent; prints before/after.

Run on the VPS: /opt/dr3/.venv/bin/python /tmp/cleanup_vehicle_reqs.py
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
    old_vids = settings.get("vehicle_ids")
    old_uiv = settings.get("use_inv_vehicle")
    old_ct = settings.get("challenge_type")
    old_st = settings.get("scoring_type")
    old_cm = settings.get("challenge_mode")
    keys = ("vehicle_ids", "use_inv_vehicle",
            "challenge_type", "scoring_type", "challenge_mode")
    changed = any(k in settings for k in keys)
    if changed:
        for k in keys:
            settings.pop(k, None)
        cur.execute("UPDATE events SET settings_json = %s WHERE id = %s",
                    (json.dumps(settings), eid))
    print(f"{'OK  ' if changed else 'NOOP'} club={club_name!r} event={eid} "
          f"({ev_name!r}) cleared vehicle_ids={old_vids!r} "
          f"use_inv_vehicle={old_uiv!r} challenge_type={old_ct!r} "
          f"scoring_type={old_st!r} challenge_mode={old_cm!r}")
conn.commit()

print("\nverify:")
cur.execute("SELECT c.name, e.id, e.settings_json FROM events e "
            "JOIN clubs c ON c.id = e.club_id WHERE c.name IN (%s,%s,%s)",
            tuple(CLUBS))
for cname, eid, sj in cur.fetchall():
    s = json.loads(sj)
    print(f"  {cname!r} event={eid} vehicle_ids={s.get('vehicle_ids')} "
          f"use_inv_vehicle={s.get('use_inv_vehicle')} "
          f"challenge_type={s.get('challenge_type', '<default-2>')} "
          f"scoring_type={s.get('scoring_type', '<default-2>')} "
          f"challenge_mode={s.get('challenge_mode', '<default-1>')}")
conn.close()
raise SystemExit(0 if ok else 1)
