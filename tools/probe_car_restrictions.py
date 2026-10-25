"""#13 investigation: dump car-restriction-relevant fields from every
upstream official challenge in the real RaceNet GetChallenges capture.

Looks for: Requirements (Type/Value), UseInvVehicle, Category, Mode,
ChallengeType, and any field mentioning vehicle/car.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.egonet import decode_stream  # noqa: E402


def u(v):
    """Unwrap egonet scalar wrappers to plain ints."""
    return getattr(v, "value", v)


bin_path = ROOT / "dr3" / "data" / "upstream_templates" / "RaceNetChallenges_GetChallenges.bin"
top = decode_stream(bin_path.read_bytes())
chals = top.get("Challenges") or []
print(f"num challenges: {len(chals)}\n")

for c in chals:
    reqs = c.get("Requirements") or []
    req_txt = ", ".join(
        f"T{u(r.get('Type'))}={u(r.get('Value'))}" for r in reqs) or "-"
    print(f"ID={u(c.get('ChallengeID'))} name={c.get('Name')!r} "
          f"type={u(c.get('ChallengeType'))} cat={u(c.get('Category'))} mode={u(c.get('Mode'))} "
          f"useInvVehicle={c.get('UseInvVehicle')} hardcore={c.get('IsHardcore')}")
    print(f"   Requirements: {req_txt}")
    # any other vehicle-ish keys on the challenge itself
    extra = {k: v for k, v in c.items()
             if any(t in k.lower() for t in ("vehicle", "car")) and v not in (0, False, None, "")}
    if extra:
        print(f"   vehicle-ish fields: {extra}")
