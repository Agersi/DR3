#!/usr/bin/env python3
"""Print the most recent StageBegin/StageComplete capture entries (local).

Reads the client's EgoNet request captures and shows, in time order, every
stage start/finish with its TyreCompound / TyresRemaining — usable the
moment a stage starts (no need to wait for the run to land in the DB).
Used together with map_tyre_compounds.py to build the compound ID -> name
map: start a stage with a known tyre, read the ID here, record it there.

Usage:  python tools/read_stage_begin.py [MINUTES] [--dir CAPTURE_DIR]

MINUTES is how far back to look (default 30).  --dir overrides the capture
directory (default %APPDATA%/DR3/captures).
"""
import json
import sys
import time
from pathlib import Path

DEFAULT_DIR = Path(__import__("os").environ.get(
    "APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "DR3" / "captures"


def main() -> int:
    args = sys.argv[1:]
    minutes = 30
    cap_dir = DEFAULT_DIR
    if "--dir" in args:
        i = args.index("--dir")
        cap_dir = Path(args[i + 1])
        del args[i:i + 2]
    if args:
        minutes = float(args[0])

    if not cap_dir.is_dir():
        print(f"capture dir not found: {cap_dir}")
        return 1

    cutoff = time.time() - minutes * 60
    rows = []
    for f in cap_dir.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                continue
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        fn = (data.get("headers") or {}).get("X-EgoNet-Function", "")
        if "StageBegin" not in fn and "StageComplete" not in fn:
            continue
        body = data.get("decoded_body") or {}
        rows.append((f.stat().st_mtime, f.name, fn, body))

    rows.sort(key=lambda r: r[0])
    print(f"== stage begin/complete captures, last {minutes:g} min "
          f"({cap_dir}) ==")
    if not rows:
        print("(none - is Verbose logging on in the DR3 GUI?)")
        return 0
    for mt, fname, fn, body in rows:
        when = time.strftime("%H:%M:%S", time.localtime(mt))
        kind = "BEGIN   " if "Begin" in fn else "COMPLETE"
        if "Begin" in fn:
            print(f"{when}  {kind} chal={body.get('ChallengeId')} "
                  f"stage={body.get('StageIndex')} veh={body.get('VehicleId')} "
                  f"livery={body.get('LiveryId')} "
                  f"COMPOUND={body.get('TyreCompound')} "
                  f"tyres_left={body.get('TyresRemaining')}")
        else:
            print(f"{when}  {kind} chal={body.get('ChallengeId')} "
                  f"stage={body.get('StageIndex')} status={body.get('RaceStatus')} "
                  f"time_ms={body.get('StageTimeMs')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
