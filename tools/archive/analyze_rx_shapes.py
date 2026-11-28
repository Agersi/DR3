"""Show challenge shapes (DisciplineId / NumberLaps) + StageBegin responses for
the session captures, to pin down which legs were RX (backlog item 17)."""
import json
from pathlib import Path

CAPTURES = Path(r"C:\Users\Agersi\AppData\Roaming\DR3\captures")
WANT = {251893, 251894, 251895, 219365, 233018, 214278, 211555, 213153}


def walk(obj):
    """Yield (path, value) for every dict/list node."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def main() -> None:
    files = sorted(CAPTURES.glob("*.json"))
    seen_shapes = {}
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        fn = (data.get("headers") or {}).get("X-EgoNet-Function", "")
        body = data.get("decoded_body") or {}
        # StageBegin responses: print the Progress we echoed back
        if "StageBegin" in fn:
            for node in walk(data.get("response", {}).get("body")):
                if isinstance(node, dict) and "Progress" in node:
                    p = node["Progress"]
                    chal = p.get("ChallengeID")
                    if chal in WANT:
                        print(
                            f"{f.name[:15]}  BEGIN-RESP chal={chal} "
                            f"compound={p.get('TyreCompound')} "
                            f"tyres={p.get('TyresRemaining')} "
                            f"veh={p.get('VehicleId')} state={p.get('State')}"
                        )
        # challenge shapes: any dict with ChallengeId + Stages
        for node in walk(data.get("response", {}).get("body")):
            if isinstance(node, dict) and "Stages" in node and "ChallengeId" in node:
                cid = node["ChallengeId"]
                if cid in WANT and cid not in seen_shapes:
                    stages = node["Stages"] or []
                    laps = [s.get("NumberLaps") for s in stages]
                    disc = node.get("DisciplineId")
                    seen_shapes[cid] = True
                    print(
                        f"{f.name[:15]}  SHAPE chal={cid} discipline={disc} "
                        f"stages={len(stages)} laps={laps}"
                    )


if __name__ == "__main__":
    main()
