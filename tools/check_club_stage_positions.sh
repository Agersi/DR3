#!/bin/bash
# Map every club stage_stats row to its position inside the event leg (read-only).
# Answers: did first-of-leg stages get splits while later stages didn't?
set -e
/opt/dr3/.venv/bin/python - <<'PYEOF'
import json, os, re

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import pymysql

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             os.environ["DATABASE_URL"])
conn = pymysql.connect(host=p.group(3), port=int(p.group(4) or 3306),
                       user=p.group(1), password=p.group(2),
                       database=p.group(5).split("?")[0])
cur = conn.cursor()

cur.execute("""SELECT id, username, vehicle_id, track_id, best_time_ms,
                      source_event_id, updated_at, splits_json IS NOT NULL
               FROM stage_stats WHERE source_type='club' ORDER BY updated_at""")
rows = cur.fetchall()
print(f"club stage_stats rows: {len(rows)}")

legs_cache = {}
def legs_of(eid):
    if eid not in legs_cache:
        cur.execute("SELECT legs_json FROM events WHERE id=%s", (eid,))
        r = cur.fetchone()
        legs_cache[eid] = json.loads(r[0] or "[]") if r else []
    return legs_cache[eid]

for rid, user, vid, track, best, eid, when, has_splits in rows:
    pos = "?"
    first_of_leg = "?"
    for i, leg in enumerate(legs_of(eid or "")):
        stages = leg.get("stages") or []
        for j, s in enumerate(stages):
            if int(s.get("track_id") or 0) == track:
                pos = f"leg{i}/stage{j}of{len(stages)}"
                first_of_leg = "FIRST" if j == 0 else "     "
    print(f"  id={rid:<4} {str(when)[:16]:<16} {user[:12]:<12} vid={vid:<5} "
          f"track={track:<5} evt={str(eid)[-8:]:<9} best={best:<9} "
          f"{pos:<16} {first_of_leg} splits={'Y' if has_splits else 'N'}")
PYEOF
