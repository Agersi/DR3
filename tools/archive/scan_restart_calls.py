r"""Scan user captures for challenge lifecycle RPCs (StartChallenge /
ResumeChallenge / StageBegin repeats) to understand the in-game restart path.

Run: python tools\scan_restart_calls.py
"""
import json
import re
from pathlib import Path

CAPDIR = Path(r"C:\Users\Agersi\AppData\Roaming\DR3\captures")


def main():
    rows = []
    for f in sorted(CAPDIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        method = None
        req = None
        if isinstance(data, dict):
            method = (data.get("headers") or {}).get("X-EgoNet-Function", "")
            req = data.get("decoded_body")
        m = str(method or "")
        if not re.search(r"StartChallenge|ResumeChallenge|StageBegin", m):
            continue
        ts = f.name[:15]
        info = ""
        if isinstance(req, dict):
            for key in ("ChallengeId", "challenge_id", "EventIndex", "StageIndex",
                        "VehicleId", "TyreCompound", "TyresRemaining"):
                if key in req:
                    info += f" {key}={req[key]}"
        rows.append((ts, m, info.strip()))

    # Group by (method) and print chronological sequence with stage repeats.
    prev = None
    for ts, m, info in rows:
        short = m.replace("RaceNetChallenges.", "")
        mark = ""
        if short == "StageBegin" and prev is not None and prev[1] == "StageBegin" and prev[2] == info:
            mark = "  <-- REPEAT of same stage"
        print(f"{ts} {short}{info}{mark}")
        prev = (ts, short, info)


if __name__ == "__main__":
    main()
