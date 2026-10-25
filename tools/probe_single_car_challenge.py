#!/usr/bin/env python3
"""Last look at #13: how does upstream encode a SINGLE-CAR restriction?

Parses a RaceNetChallenges.GetChallenges capture (JSON, response.body_decoded)
and dumps every challenge's top-level fields + Requirements, then the FULL raw
JSON of any challenge carrying a Type=0 (single-vehicle) requirement, so we can
field-diff it against our club shape and see if there is anything beyond the
Type=0 requirement that makes it safe in-game.

Run: python tools/probe_single_car_challenge.py [capture.json]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
default = ROOT / "reference" / "20261025-064333-1792907013962646300.json"
path = Path(sys.argv[1]) if len(sys.argv) > 1 else default

data = json.loads(path.read_text(encoding="utf-8"))
resp = data.get("response") or {}
decoded = resp.get("body_decoded") or {}
chals = decoded.get("Challenges") or []
print(f"capture: {path.name}")
print(f"num challenges: {len(chals)}\n")

# 1. summary table of every challenge
print("=== ALL CHALLENGES (name / type / scoring / mode / UseInvVehicle / reqs) ===")
for i, c in enumerate(chals):
    name = c.get("Name", "?")
    ct = c.get("ChallengeType")
    st = c.get("ScoringType")
    mode = c.get("Mode")
    uiv = c.get("UseInvVehicle")
    reqs = c.get("Requirements") or []
    req_str = ", ".join(f"T{r.get('Type')}={r.get('Value')}" for r in reqs) or "-"
    flag = "  <-- TYPE0" if any(r.get("Type") == 0 for r in reqs) else ""
    print(f"[{i:2}] {name:45} CT={ct} ST={st} Mode={mode} UIV={uiv} reqs=[{req_str}]{flag}")

# 2. full dump of any Type=0 challenge
print("\n\n=== FULL DUMP OF TYPE-0 (SINGLE-CAR) CHALLENGES ===")
for i, c in enumerate(chals):
    reqs = c.get("Requirements") or []
    if not any(r.get("Type") == 0 for r in reqs):
        continue
    print(f"\n########## [{i}] {c.get('Name')} (ChallengeID={c.get('ChallengeID')}) ##########")
    # top-level keys that are NOT Events/Stages (the scalar/flag fields)
    print("--- top-level scalar fields ---")
    for k, v in c.items():
        if k in ("Events", "Requirements"):
            continue
        print(f"  {k} = {v!r}")
    print("--- Requirements ---")
    print(json.dumps(reqs, indent=2))
    # events: show structure + any vehicle/class related fields
    evs = c.get("Events") or []
    print(f"--- Events ({len(evs)}) ---")
    for j, e in enumerate(evs):
        scalar = {k: v for k, v in e.items() if k not in ("Stages",)}
        print(f"  Event[{j}] scalars: {json.dumps(scalar)}")
        stages = e.get("Stages") or []
        # only show stage scalar keys once (first stage) to reveal the schema
        if j == 0 and stages:
            print(f"    Stage[0] full: {json.dumps(stages[0])}")
            print(f"    num_stages={len(stages)}")
