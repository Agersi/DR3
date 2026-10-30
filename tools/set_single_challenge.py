#!/usr/bin/env python3
"""Set/clear settings.single_challenge on a championship event (#39 experiment).

The dispatcher serves a Multi-Location Rally championship whose event carries
settings.single_challenge=true as ONE challenge with every leg in Events[]
(default off, multi mode only — see dr2server/dispatcher.py).  This tool flips
the flag on the stored settings_json so an existing web championship can be
tested without touching anything else.

Usage (on the VPS):
    /opt/dr3/.venv/bin/python tools/set_single_challenge.py EVENT_ID|CLUB_NAME ON|OFF

The first argument is either an event id (evt-...) or a club name; a club name
must match exactly one club, and every event of that club gets the flag.
Idempotent; prints before/after.
"""
import json
import os
import sys
from urllib.parse import unquote, urlparse

if len(sys.argv) != 3:
    print(__doc__)
    raise SystemExit(2)
target, value = sys.argv[1], sys.argv[2].upper()
if value not in ("ON", "OFF"):
    print(f"bad value {sys.argv[2]!r}: use ON or OFF")
    raise SystemExit(2)

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

cur = conn.cursor()
ok = True
if target.startswith("evt-"):
    cur.execute("SELECT id, name FROM events WHERE id = %s", (target,))
    rows = cur.fetchall()
    if len(rows) != 1:
        print(f"SKIP {target!r}: {len(rows)} matching events")
        raise SystemExit(1)
    event_ids = [rows[0][0]]
else:
    cur.execute("SELECT id FROM clubs WHERE name = %s", (target,))
    club_ids = [r[0] for r in cur.fetchall()]
    if len(club_ids) != 1:
        print(f"SKIP {target!r}: {len(club_ids)} matching clubs")
        raise SystemExit(1)
    cur.execute("SELECT id FROM events WHERE club_id = %s", (club_ids[0],))
    event_ids = [r[0] for r in cur.fetchall()]
    if not event_ids:
        print(f"SKIP {target!r}: no events for club {club_ids[0]}")
        raise SystemExit(1)

for eid in event_ids:
    cur.execute("SELECT id, name, settings_json FROM events WHERE id = %s", (eid,))
    row = cur.fetchone()
    if row is None:
        print(f"SKIP {eid}: no such event")
        ok = False
        continue
    _eid, ev_name, settings_json = row
    settings = json.loads(settings_json or "{}")
    old = settings.get("single_challenge")
    if value == "ON":
        settings["single_challenge"] = True
    else:
        settings.pop("single_challenge", None)
    cur.execute("UPDATE events SET settings_json = %s WHERE id = %s",
                (json.dumps(settings), eid))
    print(f"OK   event={eid} ({ev_name!r}) single_challenge: {old} -> "
          f"{settings.get('single_challenge')}")
conn.commit()

print("\nverify:")
cur.execute("SELECT id, settings_json FROM events WHERE id IN (%s)"
            % ",".join(["%s"] * len(event_ids)), tuple(event_ids))
for eid, sj in cur.fetchall():
    s = json.loads(sj or "{}")
    print(f"  {eid} single_challenge={s.get('single_challenge')} "
          f"mode={s.get('mode')}")
conn.close()
raise SystemExit(0 if ok else 1)
