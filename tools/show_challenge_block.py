"""Extract the served challenge block(s) for given ids from a capture file."""
import json
import sys
from pathlib import Path


def walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def main() -> None:
    path = Path(sys.argv[1])
    want = {int(a) for a in sys.argv[2:]} or None
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    body = (data.get("response") or {}).get("body") or {}
    for node in walk(body):
        if not isinstance(node, dict):
            continue
        cid = node.get("ChallengeID") or node.get("ChallengeId")
        if want is None or (isinstance(cid, int) and cid in want):
            events = node.get("Events") or []
            evs = []
            for ev in events:
                stages = ev.get("Stages") or []
                evs.append({
                    "loc": ev.get("LocationId"),
                    "disc": ev.get("DisciplineId"),
                    "n_stages": len(stages),
                    "laps": [s.get("NumberLaps") for s in stages],
                })
            info = {
                "challenge_id": cid,
                "name": node.get("Name"),
                "type": node.get("ChallengeType"),
                "requirements": node.get("Requirements"),
                "events": evs,
            }
            print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
