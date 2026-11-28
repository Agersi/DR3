"""Scan DR3 capture files for the StageBegin/StageComplete sequence.

Prints, in timestamp order: challenge id, stage index, vehicle, tyre compound /
tyres remaining (request side) so we can see what the game carries over across
an RX -> rally transition (backlog item 17).
"""
import json
import sys
from pathlib import Path

CAPTURES = Path(r"C:\Users\Agersi\AppData\Roaming\DR3\captures")


def main() -> None:
    files = sorted(CAPTURES.glob("*.json"))
    rows = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        fn = (data.get("headers") or {}).get("X-EgoNet-Function", "")
        if "StageBegin" not in fn and "StageComplete" not in fn:
            continue
        body = data.get("decoded_body") or {}
        rows.append((f.name, fn, body))
    rows.sort(key=lambda r: r[0])
    for fname, fn, body in rows:
        if "Begin" in fn:
            print(
                f"{fname[:15]}  BEGIN   chal={body.get('ChallengeId')} "
                f"stage={body.get('StageIndex')} veh={body.get('VehicleId')} "
                f"livery={body.get('LiveryId')} compound={body.get('TyreCompound')} "
                f"tyres={body.get('TyresRemaining')}"
            )
        else:
            print(
                f"{fname[:15]}  COMPLETE chal={body.get('ChallengeId')} "
                f"stage={body.get('StageIndex')} status={body.get('RaceStatus')} "
                f"time={body.get('StageTimeMs')}"
            )


if __name__ == "__main__":
    sys.exit(main())
