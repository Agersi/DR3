#!/usr/bin/env python3
"""Inspect championship points allocation for a club's events.

Lists the club's events (mode / scoring system / time window) and, using the
app's own scoring code, prints every participant's per-round position, DNF
stage count and points. With an optional USERNAME the raw stage entries of
that user are dumped (time_ms / race_status / penalties) so DNF marking can
be verified against what the scorer sees.

Run on the VPS:
    /opt/dr3/.venv/bin/python inspect_champ_points.py CLUB_ID [USERNAME]
"""
import json
import os
import re
import sys

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, "/opt/dr3/web")

import pymysql  # noqa: E402
from app.scoring import (  # noqa: E402
    _range_standings,
    _ran_any_stage,
    championship_points_breakdown,
    championship_points_rounds,
    flat_stage_bogeys_ms,
    scoring_system_for,
)
from app.tables import normalize_championship  # noqa: E402

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             os.environ["DATABASE_URL"])
conn = pymysql.connect(host=p.group(3), port=int(p.group(4) or 3306),
                       user=p.group(1), password=p.group(2),
                       database=p.group(5).split("?")[0])
cur = conn.cursor()

club_id = sys.argv[1] if len(sys.argv) > 1 else None
user_filter = sys.argv[2] if len(sys.argv) > 2 else None
if not club_id:
    sys.exit("usage: inspect_champ_points.py CLUB_ID [USERNAME]")

cur.execute("SELECT id, name FROM clubs WHERE id=%s", (club_id,))
row = cur.fetchone()
if not row:
    sys.exit(f"unknown club: {club_id}")
print(f"club: {row[0]} ({row[1]})")

cur.execute(
    "SELECT id, name, settings_json, legs_json, start_time, end_time, active "
    "FROM events WHERE club_id=%s ORDER BY start_time", (club_id,))
events = cur.fetchall()
if not events:
    sys.exit("no events for this club")

for eid, name, settings_json, legs_json, start_t, end_t, active in events:
    event = {
        'id': eid, 'name': name,
        'settings': json.loads(settings_json or '{}'),
        'events': json.loads(legs_json or '[]'),
        'start_time': start_t.isoformat() if start_t else None,
        'end_time': end_t.isoformat() if end_t else None,
    }
    champ = normalize_championship(event)
    system = scoring_system_for(champ)
    print(f"\n== {eid} \"{name}\" mode={champ['mode']} scoring={system} "
          f"power_stage={bool(champ['settings'].get('power_stage'))} "
          f"active={bool(active)}")
    print(f"   window: {event['start_time']} -> {event['end_time']}")

    cur.execute(
        "SELECT username, vehicle_id, stages_json FROM club_results "
        "WHERE event_id=%s", (eid,))
    entries = []
    raw_by_user = {}
    for u, vid, sj in cur.fetchall():
        stages = json.loads(sj or '[]')
        entries.append({'username': u, 'vehicle_id': vid, 'stages': stages})
        raw_by_user[(u, vid)] = stages

    if not entries:
        print("   (no results)")
        continue

    breakdown = championship_points_breakdown(event, entries)
    rounds = championship_points_rounds(event)
    bogeys = flat_stage_bogeys_ms(event)
    info: dict[str, list] = {}
    for rnd in rounds:
        rows = _range_standings(entries, rnd['offset'], rnd['count'], bogeys)
        for pos, row in enumerate(
                (r for r in rows if _ran_any_stage(r)), start=1):
            info.setdefault(row['username'], [None] * len(rounds))
            info[row['username']][rnd['index']] = (pos, row['dnf_stages'])

    def vid_for(u):
        for e in entries:
            if e['username'] == u:
                return e.get('vehicle_id')
        return None

    for u in sorted(breakdown):
        mark = ' <<' if user_filter and u == user_filter else ''
        cells = []
        for i, rnd in enumerate(rounds):
            tag = rnd['label']
            pos_dnf = (info.get(u) or [None] * len(rounds))[i]
            c = breakdown[u][i]
            pts = c.get('points', 0) + (c.get('bonus') or 0)
            if pos_dnf is None:
                cells.append(f"{tag} -")
            else:
                pos, dnf = pos_dnf
                dnf_s = f" dnf={dnf}" if dnf else ''
                cells.append(f"{tag} p{pos}{dnf_s} pts={pts}")
        total = sum(c.get('points', 0) + (c.get('bonus') or 0)
                    for c in breakdown[u])
        print(f"   {u} (v{vid_for(u) or '?'}): " + " | ".join(cells)
              + f"  TOTAL={total}{mark}")

    if user_filter:
        for (u, vid), stages in raw_by_user.items():
            if u != user_filter:
                continue
            print(f"\n-- {u} (v{vid or '?'}) raw stages:")
            for i, s in enumerate(stages):
                if not isinstance(s, dict):
                    print(f"   s{i+1}: <missing>")
                    continue
                t = int(s.get('time_ms') or 0)
                rs = int(s.get('race_status', 0) or 0)
                pen = int(s.get('penalty_ms') or 0)
                flag = ' DNF' if rs else ''
                print(f"   s{i+1}: time={t}ms rs={rs}{flag} "
                      f"penalty={pen}ms")
