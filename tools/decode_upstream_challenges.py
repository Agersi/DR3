"""Dump Challenge/Event/Stage LeaderboardIds from the upstream GetChallenges
template (real RaceNet capture) to learn the official id schemes."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.egonet import decode_stream  # noqa: E402

bin_path = ROOT / "dr3" / "data" / "upstream_templates" / "RaceNetChallenges_GetChallenges.bin"
data = bin_path.read_bytes()
top = decode_stream(data)
print("top-level keys:", list(top.keys()))

chals = top.get("Challenges") or []
print(f"num challenges: {len(chals)}")
for c in chals:
    cid = c.get("ChallengeID", c.get("challenge_id"))
    lb = c.get("LeaderboardId", c.get("leaderboard_id"))
    events = c.get("Events") or []
    print(f"\n=== ChallengeID={cid} LeaderboardId={lb} num_events={len(events)} name_key={c.get('Name')}")
    for e in events:
        eid = e.get("EventID", e.get("event_id"))
        elb = e.get("LeaderboardId", e.get("leaderboard_id"))
        stages = e.get("Stages") or []
        print(f"  EventID={eid} LeaderboardId={elb} num_stages={len(stages)}")
        for s in stages:
            sid = s.get("StageID", s.get("stage_id"))
            slb = s.get("LeaderboardId", s.get("leaderboard_id"))
            trk = s.get("TrackModelID", s.get("track_model_id"))
            print(f"    StageID={sid} LeaderboardId={slb} Track={trk}")
