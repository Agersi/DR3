#!/usr/bin/env python3
"""Map every tyre compound ID seen in stage_runs to its context (read-only).

For each distinct ``tyre_compound`` value the tool prints: run count + date
span, source types, locations (with counts), top tracks, vehicle classes,
weather presets and the club events it was driven in.  Names are resolved
from dr2server.game_data, and each ID is annotated with its confirmed name
from TYRE_COMPOUND_NAMES below.

Run on the VPS:
    /opt/dr3/.venv/bin/python map_tyre_compounds.py [COMPOUND ...] [--top N]

Without arguments every compound is shown; positional args restrict the
output to the given IDs.  ``--top N`` controls how many tracks are listed
per compound (default 8).
"""
import os
import re
import sys
from collections import defaultdict

ENV_PATH = "/opt/dr3/web/.env"
with open(ENV_PATH) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, "/opt/dr3")

import pymysql  # noqa: E402

try:
    from dr2server.game_data import (  # noqa: E402
        LOCATIONS, TRACKS, VEHICLE_CLASSES, WEATHER_PRESETS)
except Exception as exc:  # degrade to raw ids if the import fails
    print(f"[warn] game_data unavailable ({exc}); showing raw ids")
    LOCATIONS = TRACKS = VEHICLE_CLASSES = WEATHER_PRESETS = {}

# Tyre compound ID -> name, confirmed in-game 2026-11-14 by the user
# (all surface/class combinations tested + DB cross-check).
TYRE_COMPOUND_NAMES = {
    1: "Soft (Gravel)",
    2: "Medium (Gravel)",
    3: "Hard (Gravel)",
    4: "Wet (Tarmac)",
    5: "?",
    6: "Soft (Tarmac + Monaco)",
    7: "Medium (Tarmac)",
    8: "Hard (Tarmac)",
    9: "Winter (Monaco)",
    10: "Snow (Sweden)",
    11: "RX Wet",
    12: "?",
    13: "RX Only (internal value >= _RX_TYRE_COMPOUND_MIN)",
}


def loc_name(loc_id):
    meta = LOCATIONS.get(int(loc_id))
    return meta["display_name"] if meta else f"loc{loc_id}"


def track_name(track_id):
    meta = TRACKS.get(int(track_id))
    return meta["name"] if meta else f"track{track_id}"


def main():
    args = sys.argv[1:]
    top_n = 8
    if "--top" in args:
        i = args.index("--top")
        top_n = int(args[i + 1])
        del args[i:i + 2]
    only = {int(a) for a in args} if args else None

    p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
                 os.environ["DATABASE_URL"])
    user, pw, host, port, dbname = (p.group(1), p.group(2), p.group(3),
                                    int(p.group(4) or 3306), p.group(5).split("?")[0])
    conn = pymysql.connect(host=host, port=port, user=user, password=pw,
                           database=dbname)
    cur = conn.cursor()

    def pairs(sql):
        """Run a GROUP BY (tyre_compound, X) query -> {compound: [(x, count)]}."""
        cur.execute(sql)
        out = defaultdict(list)
        for comp, x, n in cur.fetchall():
            if only and int(comp) not in only:
                continue
            out[int(comp)].append((x, n))
        for k in out:
            out[k].sort(key=lambda t: -t[1])
        return out

    cur.execute(
        "SELECT tyre_compound, COUNT(*), MIN(received_at), MAX(received_at),"
        " GROUP_CONCAT(DISTINCT source_type ORDER BY source_type)"
        " FROM stage_runs WHERE tyre_compound > 0"
        " GROUP BY tyre_compound ORDER BY tyre_compound")
    summary = {int(c): (n, lo, hi, src) for c, n, lo, hi, src in cur.fetchall()
               if not only or int(c) in only}

    by_loc = pairs("SELECT tyre_compound, location_id, COUNT(*) FROM stage_runs"
                   " WHERE tyre_compound > 0 AND location_id IS NOT NULL"
                   " AND location_id > 0 GROUP BY 1,2")
    by_track = pairs("SELECT tyre_compound, track_model_id, COUNT(*)"
                     " FROM stage_runs WHERE tyre_compound > 0"
                     " AND track_model_id IS NOT NULL AND track_model_id > 0"
                     " GROUP BY 1,2")
    by_class = pairs("SELECT tyre_compound, vehicle_class_id, COUNT(*)"
                     " FROM stage_runs WHERE tyre_compound > 0"
                     " AND vehicle_class_id IS NOT NULL AND vehicle_class_id > 0"
                     " GROUP BY 1,2")
    by_weather = pairs("SELECT tyre_compound, weather_preset_id, COUNT(*)"
                       " FROM stage_runs WHERE tyre_compound > 0"
                       " AND weather_preset_id > 0 GROUP BY 1,2")

    cur.execute(
        "SELECT r.tyre_compound, e.name, e.location, COUNT(*)"
        " FROM stage_runs r JOIN events e ON e.id = r.event_id"
        " WHERE r.tyre_compound > 0 AND r.source_type = 'club'"
        " GROUP BY 1,2,3 ORDER BY 1, 4 DESC")
    by_event = defaultdict(list)
    for comp, name, loc, n in cur.fetchall():
        if only and int(comp) not in only:
            continue
        by_event[int(comp)].append((f"{name} ({loc})" if loc else name, n))

    conn.close()

    print(f"== tyre compound map — {len(summary)} distinct ids ==")
    for comp in sorted(summary):
        n, lo, hi, src = summary[comp]
        known = TYRE_COMPOUND_NAMES.get(comp)
        tag = f"  <-- {known}" if known else ""
        print(f"\ncompound {comp}   {n} runs  {lo:%Y-%m-%d}..{hi:%Y-%m-%d}"
              f"  [{src or '?'}]{tag}")
        if by_loc.get(comp):
            locs = ", ".join(f"{loc_name(x)} ({c})" for x, c in by_loc[comp])
            print(f"  locations: {locs}")
        if by_track.get(comp):
            tr = by_track[comp][:top_n]
            rest = len(by_track[comp]) - len(tr)
            tracks = ", ".join(f"{track_name(x)} ({c})" for x, c in tr)
            print(f"  tracks:    {tracks}" + (f"  (+{rest} more)" if rest > 0 else ""))
        if by_class.get(comp):
            cls = ", ".join(
                f"{VEHICLE_CLASSES.get(x, 'class' + str(x))}={x}"
                for x, c in by_class[comp])
            print(f"  classes:   {cls}")
        if by_weather.get(comp):
            wx = ", ".join(f"{WEATHER_PRESETS.get(x, 'wx' + str(x))} ({c})"
                           for x, c in by_weather[comp])
            print(f"  weather:   {wx}")
        if by_event.get(comp):
            evs = ", ".join(f"{e} ({c})" for e, c in by_event[comp][:10])
            print(f"  club evts: {evs}")


if __name__ == "__main__":
    main()
