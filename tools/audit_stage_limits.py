#!/usr/bin/env python3
"""Audit stage time limits from captured post-wreck leaderboard queries.

After terminal damage the game queries the stage leaderboard with
PlayerBest = the stage's time limit (the value our pre-persist stores).
This tool scans a captures directory for every GetLeaderboardEntries
request with PlayerBest > 0 and prints one row per capture:

    date  base_challenge  flat_stage  player_best_ms  leaderboard_id

Stage-level leaderboard ids are ``base*10 + event_index*1_000_000 + stage``
(flat ordinal, see dispatcher._leaderboard), so the rows can be correlated
with an event's legs to derive per-stage limits.

Usage: python tools/audit_stage_limits.py [CAPTURES_DIR]
"""
import json
import sys
from pathlib import Path

DEFAULT_DIR = r"C:\Users\Agersi\AppData\Roaming\DR3\captures"


def main() -> None:
    cap_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(DEFAULT_DIR)
    rows = []
    for f in sorted(cap_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        body = data.get("decoded_body") or {}
        best = int(body.get("PlayerBest") or 0)
        if best <= 0:
            continue
        lb_id = int(body.get("LeaderboardId") or 0)
        date = f.name[:8]
        rows.append((date, best, lb_id))
    for r in rows:
        print(f"date={r[0]} limit_ms={r[1]} lb_id={r[2]}")
    print(f"total: {len(rows)} post-wreck leaderboard query(ies)")


if __name__ == "__main__":
    main()
