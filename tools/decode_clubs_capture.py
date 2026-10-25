"""Dump club challenge Requirements from the real RaceNet Clubs_GetClubs capture."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.egonet import decode_stream  # noqa: E402


def plain(o):
    if isinstance(o, dict):
        return {k: plain(v) for k, v in o.items()}
    if isinstance(o, list):
        return [plain(v) for v in o]
    if hasattr(o, "value"):
        return o.value
    return o


data = (ROOT / "dr3" / "data" / "upstream_templates" / "Clubs_GetClubs.bin").read_bytes()
top = decode_stream(data)
print("top-level keys:", list(top.keys()))

clubs = top.get("Clubs") or []
for c in clubs:
    print(f"\nCLUB id={c.get('ClubID')} name={c.get('Name')!r} events={c.get('AmountOfEvents')}")

chals = top.get("Challenges") or []
print(f"\nnum challenges: {len(chals)}")
for ch in chals:
    reqs = plain(ch.get("Requirements"))
    events = ch.get("Events") or []
    print(f"\nCHAL id={ch.get('ChallengeID')} type={ch.get('ChallengeType')} "
          f"scoring={ch.get('ScoringType')} cat={ch.get('Category')} mode={ch.get('Mode')} "
          f"use_inv={ch.get('UseInvVehicle')} reqs={reqs}")
    for e in events:
        print("  EVENT keys:", {k: plain(v) if not isinstance(v, (dict, list)) else type(v).__name__
                                for k, v in e.items()})
        stages = e.get("Stages") or []
        for s in stages:
            print("    STAGE keys:", {k: plain(v) for k, v in s.items()})
