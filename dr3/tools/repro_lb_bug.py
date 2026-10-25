"""Repro for backlog #10 — multi-round championship shows wrong data on the
in-game leaderboard screen for rounds past index 0.

Simulates the game-side flow locally: serves Clubs.GetClubs for a 2-rally
championship (layout [3, 2]), then queries RaceNetLeaderboard.GetLeaderboardEntries
with the stage LeaderboardIds of rally 0 and rally 1 exactly as the served
Challenge carries them, and prints what the game would display.

Run: python tools\repro_lb_bug.py   (from dr3/, system or venv Python)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dr2server.dispatcher import RpcDispatcher, _stable_int_id  # noqa: E402
from dr2server.models import Int64  # noqa: E402
from dr2server.game_data import Location, Track, VehicleClass  # noqa: E402

EVENT_ID = "evt-1"
CLUB_ID = "club-1"


def _stage(track):
    return {
        "track_id": int(track),
        "conditions_id": 1,
        "surface_deg": None,
        "service_area": None,
    }


# Rally 0: 3 stages (Germany), rally 1: 2 stages (Germany). Layout [3, 2].
EVENT = {
    "id": EVENT_ID,
    "club_id": CLUB_ID,
    "name": "Test Club",
    "active": True,
    "car_class": "Group B 4WD",
    "mode": "multi",
    "settings": {},
    "start_time": "2026-10-01T00:00:00",
    "end_time": "2027-01-01T00:00:00",
    "events": [
        {
            "location": "Germany",
            "car_class": "Group B 4WD",
            "stages": [_stage(Track.OBERSTEIN), _stage(Track.FRAUENBERG),
                       _stage(Track.WALDAUFSTIEG)],
        },
        {
            "location": "Germany",
            "car_class": "Group B 4WD",
            "stages": [_stage(Track.HAMMERSTEIN), _stage(Track.RUSCHBERG)],
        },
    ],
}


def _entry(name, times):
    stages = []
    for i in range(5):
        t = times.get(i)
        stages.append({"time_ms": t, "penalties_ms": 0} if t
                      else {"time_ms": 0, "penalties_ms": 0})
    return {
        "username": name,
        "vehicle_id": 482,
        "total_time_ms": sum(times.values()),
        "stages": stages,
    }


# driverA: finished rally 0 + stage 0 of rally 1.
# driverB: finished everything (all 5 flat stages).
ENTRIES = [
    _entry("driverA", {0: 100000, 1: 90000, 2: 80000, 3: 70000}),
    _entry("driverB", {0: 95000, 1: 92000, 2: 85000, 3: 60000, 4: 55000}),
]


class FakeClient:
    def get_clubs(self):
        return {
            "clubs": [{"id": CLUB_ID, "name": "Test Club", "created_by": "me"}],
            "events": [EVENT],
        }

    def resolve_location_id(self, name):
        return int(Location.GERMANY) if name == "Germany" else None

    def tracks_for_location(self, lid):
        if lid == int(Location.GERMANY):
            return [int(t) for t in (Track.OBERSTEIN, Track.FRAUENBERG,
                                     Track.WALDAUFSTIEG, Track.HAMMERSTEIN,
                                     Track.RUSCHBERG)]
        return []

    def resolve_vclass_id(self, label):
        return int(VehicleClass.GROUP_B_4WD)

    def get_event(self, eid):
        return EVENT if eid == EVENT_ID else None

    def get_leaderboard(self, eid):
        return ENTRIES if eid == EVENT_ID else []

    def get_my_progress(self):
        # "me" completed flat stages 0..3 (all of rally 0 + stage 0 of rally 1)
        # -> the championship is now ON rally 1 (round 2).
        return {"ok": True, "events": [{
            "event_id": EVENT_ID,
            "completed_stages": [
                {"stage_index": i, "time_ms": 99000 - i * 1000,
                 "penalties_ms": 0} for i in range(4)
            ],
            "total_time_ms": sum(99000 - i * 1000 for i in range(4)),
            "attempts_used": 0,
        }]}

    def test_token(self):
        return "me"


def show(title, resp):
    print(f"\n=== {title} ===")
    entries = resp.get("Entries", [])
    print(f"TotalEntries={resp.get('TotalEntries')} PlayerRank={resp.get('PlayerRank')}")
    for e in entries:
        p = e["Presence"]
        pb = getattr(e.get("PersonalBest"), "value", e.get("PersonalBest"))
        cb = getattr(e.get("CumulativeBest"), "value", e.get("CumulativeBest"))
        td = getattr(e.get("TimeDiff"), "value", e.get("TimeDiff"))
        print(f"  rank={e.get('Rank')} {p.get('Name')!r} "
              f"PersonalBest={pb/1000:.1f}s CumulativeBest={cb/1000:.1f}s diff={td/1000:+.1f}s")


def main():
    d = RpcDispatcher(account_store=None, api_client=FakeClient())

    clubs = d.dispatch("Clubs.GetClubs", {})
    chals = clubs.get("Challenges", [])
    assert len(chals) == 1, f"expected 1 challenge, got {len(chals)}"
    chal = chals[0]
    chal_id = chal["ChallengeID"]
    evs = chal["Events"]
    print(f"served challenge: id={chal_id} events={len(evs)} "
          f"club EventIndex={clubs['Clubs'][0].get('EventIndex')}")
    for ev in evs:
        for s in ev.get("Stages", []):
            print(f"  event EventId={ev.get('EventId')} stage {s.get('StageId')}: "
                  f"LeaderboardId={s.get('LeaderboardId')} track={s.get('TrackModelId')}")

    base = _stable_int_id(EVENT_ID, base=200000)
    print(f"\nbase_chal_id={base} map={ {k: v for k, v in d._challenge_event_map.items()} } "
          f"subevent_map={dict(d._challenge_subevent_map)}")

    # Round 1 (rally 0), stage 0 — the case that works today.
    show("ROUND 1 / stage 0 board (lb id base*10+0)",
         d.dispatch("RaceNetLeaderboard.GetLeaderboardEntries",
                    {"LeaderboardId": Int64(base * 10 + 0)}))

    # Round 2 (rally 1), stage 0 — the case that shows WRONG data in-game.
    show("ROUND 2 / stage 0 board (lb id base*10+1_000_000)",
         d.dispatch("RaceNetLeaderboard.GetLeaderboardEntries",
                    {"LeaderboardId": Int64(base * 10 + 1_000_000)}))

    # Round 2, stage 1.
    show("ROUND 2 / stage 1 board (lb id base*10+1_000_000+1)",
         d.dispatch("RaceNetLeaderboard.GetLeaderboardEntries",
                    {"LeaderboardId": Int64(base * 10 + 1_000_000 + 1)}))

    # Event-level (challenge) board for the served round-2 challenge.
    show("ROUND 2 / event-level board (lb id chal_id+800000)",
         d.dispatch("RaceNetLeaderboard.GetLeaderboardEntries",
                    {"LeaderboardId": Int64(chal_id + 800000)}))

    # Championship points board.
    club_int = getattr(clubs["Clubs"][0]["Id"], "value", clubs["Clubs"][0]["Id"])
    show("Clubs.GetChampionshipLeaderboard (points)",
         d.dispatch("Clubs.GetChampionshipLeaderboard",
                    {"ClubId": Int64(club_int), "StartRank": 0, "Limit": 50}))


if __name__ == "__main__":
    main()
