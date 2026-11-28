r"""Scan capture dirs for penalty-related protocol fields.

Answers, from raw captured traffic (NOT our parser's view):
  * every X-EgoNet-Function seen in the captures
  * every occurrence of RepairPenalty / HasRepaired with its value
    (these live in server->client Progress structs)
  * the full key set of game->server StageComplete requests
    (proves whether the game ever reports a penalty itself)

Run:
  python tools\scan_penalty_captures.py [CAPDIR ...]

Defaults to the local DR3 capture dir and the upstream split captures.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import Counter


def default_dirs() -> list[str]:
    dirs = []
    local = os.path.join(os.environ.get("APPDATA", ""), "DR3", "captures")
    if local and os.path.isdir(local):
        dirs.append(local)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    up = os.path.join(here, "build", "upstream_split_captures")
    if os.path.isdir(up):
        dirs.append(up)
    return dirs


def main() -> int:
    dirs = sys.argv[1:] or default_dirs()
    funcs: Counter = Counter()
    penalty_hits: list[tuple] = []
    sc_keys: Counter = Counter()
    sb_keys: Counter = Counter()
    files = 0
    for d in dirs:
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            try:
                data = json.load(open(f, encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            files += 1
            hdr = data.get("headers") or {}
            fn = str(hdr.get("X-EgoNet-Function", "?"))
            funcs[fn] += 1
            req = data.get("decoded_body")
            if isinstance(req, dict):
                if "StageComplete" in fn:
                    sc_keys[tuple(sorted(req.keys()))] += 1
                if "StageBegin" in fn:
                    sb_keys[tuple(sorted(req.keys()))] += 1
            blob = json.dumps(data)
            if "RepairPenalty" in blob or "HasRepaired" in blob:
                vals = set(re.findall(r'"RepairPenalty":\s*(\d+)', blob))
                hps = set(re.findall(r'"HasRepaired":\s*(true|false)', blob))
                penalty_hits.append((os.path.basename(f)[:15], fn, sorted(vals), sorted(hps)))

    print(f"files scanned: {files}  dirs: {dirs}")
    print("\n== X-EgoNet-Function counts ==")
    for fn, n in funcs.most_common():
        print(f"  {n:5d}  {fn}")

    print("\n== RepairPenalty / HasRepaired occurrences ==")
    if not penalty_hits:
        print("  (none)")
    non_zero = [h for h in penalty_hits if any(v != "0" for v in h[2])]
    for ts, fn, vals, hps in penalty_hits[:60]:
        mark = "   <-- NON-ZERO" if (ts, fn, vals, hps) in [(x[0], x[1], x[2], x[3]) for x in non_zero] else ""
        print(f"  {ts}  {fn:45s} RepairPenalty={vals} HasRepaired={hps}{mark}")
    if len(penalty_hits) > 60:
        print(f"  ... and {len(penalty_hits) - 60} more")
    print(f"  total occurrences: {len(penalty_hits)}, non-zero RepairPenalty: {len(non_zero)}")

    print("\n== game->server StageComplete request key sets ==")
    for keys, n in sc_keys.most_common():
        print(f"  x{n}: {', '.join(keys)}")
    print("\n== game->server StageBegin request key sets ==")
    for keys, n in sb_keys.most_common():
        print(f"  x{n}: {', '.join(keys)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
