#!/usr/bin/env python3
"""Generate dr3/dr2server/stage_time_limits.csv from the game_data track catalog.

The CSV lists every stage/circuit in the Track catalog (rally + rallycross)
with its length, plus an empty ``max_time_ms`` column for the game's per-stage
time limit (the value the game pre-persists after terminal damage).  Re-running
this tool preserves any max_time_ms values already filled in (merged by
track_id), so it is safe to regenerate the catalog rows later.

Usage: python tools/generate_stage_time_limits.py
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.game_data import Track, _TRACK_META  # noqa: E402

OUT = ROOT / "dr3" / "dr2server" / "stage_time_limits.csv"


def main() -> None:
    existing = {}
    if OUT.exists():
        with open(OUT, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                tid = (row.get("track_id") or "").strip()
                val = (row.get("max_time_ms") or "").strip()
                if tid and val:
                    existing[tid] = val

    rows = []
    for t in sorted(Track, key=lambda t: (
            _TRACK_META[t].get("discipline", "rally"),
            _TRACK_META[t]["location"].display_name.lower(),
            int(t))):
        meta = _TRACK_META[t]
        loc = meta["location"]
        rows.append({
            "track_id": str(int(t)),
            "discipline": meta.get("discipline", "rally"),
            "location": loc.display_name,
            "name": meta["display_name"],
            "length_km": f"{meta.get('length_km', 0.0):.2f}",
            "max_time_ms": existing.get(str(int(t)), ""),
        })

    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "track_id", "discipline", "location", "name", "length_km",
            "max_time_ms"])
        writer.writeheader()
        writer.writerows(rows)

    filled = sum(1 for r in rows if r["max_time_ms"])
    print(f"wrote {OUT} — {len(rows)} stages ({filled} with max_time_ms)")


if __name__ == "__main__":
    main()
