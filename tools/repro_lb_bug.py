"""Repro for backlog #10 — multi-round championship shows wrong data on the
in-game leaderboard screen for rounds past index 0.

Mirrors the LIVE VPS state (captured via tools/inspect_results.sh):

  Club "Just a test" (club-120d10dc), event evt-32aceb25, layout [3, 3]:
    leg 0 Poland:   Jezioro Rotcze(620) Marynka(617) Jezioro Lukie(625)
    leg 1 Germany:  Verbundsring Reverse(498) Innerer Feld-Sprint umgekehrt(497)
                    Ruschberg(494)
  Club "RX Test" (club-1bae6e2f), event evt-a4b4f489, layout [1, 1].

  Agersi (local player) completed flat stages 0..3: Poland x3 + Germany stage 0.
  Flat times: 0=336880 1=226489 2=167643 3=182274.

The served ids for the active round-2 (Germany) challenge (base = 275619):
  stage-level    base*10 + 1_000_000 + si   (Stage.leaderboard_id)
  challenge-level (base+1) + 800000         (Challenge.leaderboard_id)
  event-level    base + 900000 + 10_000_000 (Event.leaderboard_id)

Queries all three and prints which web event each one resolves to.

Run: python tools\\repro_lb_bug.py   (from D:\\CODE\\DR2)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.dispatcher import RpcDispatcher  # noqa: E402
from dr2server.models import Int64  # noqa: E402
from dr2server.game_data import Location, Track, VehicleClass  # noqa: E402

CLUB_A = "club-120d10dc"   # Just a test
EVENT_A = "evt-32aceb25"   # layout [3,3] Poland/Germany
CLUB_B = "club-1bae6e2f"   # RX Test
EVENT_B = "evt-a4b4f489"   # layout [1,1]

AGERSI_TIMES = {0: 336880, 1: 226489, 2: 167643, 3: 182274}
BOB_TIMES = {0: 200000, 1: 210000}


def _stage(track):
    return {"track_id": int(track), "conditions_id": 1,
            "surface_deg": None, "service_area": None}


EVENT_A_D = {
    "id": EVENT_A, "club_id": CLUB_A, "name": "Just a test name",
    "active": True, "car_class": "Group B 4WD", "mode": "multi",
    "settings": {},
    "start_time": "2026-10-01T00:00:00", "end_time": "2027-01-01T00:00:00",
    "events": [
        {"location": "Poland", "car_class": "Group B 4WD",
         "stages": [_stage(Track.JEZIORO_ROTCZE), _stage(Track.MARYNKA),
                    _stage(Track.JEZIORO_LUKIE)]},
        {"location": "Germany", "car_class": "Group B 4WD",
         "stages": [_stage(Track.VERBUNDSRING_REVERSE),
                    _stage(Track.INNERER_FELD_SPRINT_UMGEKEHRT),
                    _stage(Track.RUSCHBERG)]},
    ],
}

EVENT_B_D = {
    "id": EVENT_B, "club_id": CLUB_B, "name": "RX Test 1",
    "active": True, "car_class": "Group B 4WD", "mode": "multi",
    "settings": {},
    "start_time": "2026-10-01T00:00:00", "end_time": "2027-01-01T00:00:00",
    "events": [
        {"location": "Germany", "car_class": "Group B 4WD",
         "stages": [_stage(Track.VERBUNDSRING_REVERSE)]},
        {"location": "Germany", "car_class": "Group B 4WD",
         "stages": [_stage(Track.INNERER_FELD_SPRINT_UMGEKEHRT)]},
    ],
}


def _entry(name, times, total_slots=6):
    stages = []
    for i in range(total_slots):
        t = times.get(i)
        stages.append({"time_ms": t or 0, "penalties_ms": 0})
    return {"username": name, "vehicle_id": 482,
            "total_time_ms": sum(times.values()), "stages": stages}


ENTRIES_A = [_entry("Agersi", AGERSI_TIMES)]
ENTRIES_B = [_entry("bob", BOB_TIMES)]


class FakeClient:
    def __init__(self):
        self.lb_requests = []

    def get_clubs(self):
        # Live web order: soonest-expiring live/upcoming championship first.
        # Just a test's window is assumed to come first (round 1 looked fine).
        return {
            "clubs": [
                {"id": CLUB_A, "name": "Just a test", "created_by": "Agersi"},
                {"id": CLUB_B, "name": "RX Test", "created_by": "bob"},
            ],
            "events": [EVENT_A_D, EVENT_B_D],
        }

    def resolve_location_id(self, name):
        return int(getattr(Location, name.upper())) if name in (
            "Poland", "Germany") else None

    def tracks_for_location(self, lid):
        g = int(Location.GERMANY)
        p = int(Location.POLAND)
        if lid == g:
            return [int(t) for t in (Track.VERBUNDSRING_REVERSE,
                                     Track.INNERER_FELD_SPRINT_UMGEKEHRT,
                                     Track.RUSCHBERG)]
        if lid == p:
            return [int(t) for t in (Track.JEZIORO_ROTCZE, Track.MARYNKA,
                                     Track.JEZIORO_LUKIE)]
        return []

    def resolve_vclass_id(self, label):
        return int(VehicleClass.GROUP_B_4WD)

    def get_event(self, eid):
        if eid == EVENT_A:
            return EVENT_A_D
        if eid == EVENT_B:
            return EVENT_B_D
        return None

    def get_leaderboard(self, eid):
        self.lb_requests.append(eid)
        if eid == EVENT_A:
            return ENTRIES_A
        if eid == EVENT_B:
            return ENTRIES_B
        return []

    def get_my_progress(self):
        return {"ok": True, "events": [{
            "event_id": EVENT_A,
            "completed_stages": [
                {"stage_index": i, "time_ms": AGERSI_TIMES[i],
                 "penalties_ms": 0} for i in sorted(AGERSI_TIMES)
            ],
            "total_time_ms": sum(AGERSI_TIMES.values()),
            "attempts_used": 0,
        }]}

    def test_token(self):
        return "Agersi"


def show(title, resp, client):
    print(f"\n=== {title} ===")
    hits = client.lb_requests[-1:]
    print(f"  web get_leaderboard called with: {hits}")
    entries = resp.get("Entries", [])
    print(f"  TotalEntries={resp.get('TotalEntries')} "
          f"PlayerRank={resp.get('PlayerRank')}")
    for e in entries:
        p = e["Presence"]
        cb = getattr(e.get("CumulativeBest"), "value", 0) or 0
        print(f"    rank={e.get('Rank')} {p.get('Name')!r} "
              f"CumulativeBest={cb/1000:.3f}s")


def main():
    client = FakeClient()
    d = RpcDispatcher(account_store=None, api_client=client)

    clubs = d.dispatch("Clubs.GetClubs", {})
    chals = {c.get("Name"): c for c in clubs.get("Challenges", [])}
    chal_a = chals.get("Just a test name") or {}
    evs = chal_a.get("Events", [])
    served_chal_id = chal_a.get("ChallengeID")
    print(f"served 'Just a test' challenge: id={served_chal_id} "
          f"club EventIndex={clubs['Clubs'][0].get('EventIndex')}")
    for ev in evs:
        print(f"  served EventId={ev.get('EventId')} "
              f"Event.LeaderboardId={ev.get('LeaderboardId')}")
        for s in ev.get("Stages", []):
            print(f"    stage {s.get('StageId')}: "
                  f"LeaderboardId={s.get('LeaderboardId')} "
                  f"track={s.get('TrackModelId')}")

    base_a = 275619  # _stable_int_id(EVENT_A, base=200000)
    print(f"\nmaps: event_map={dict(d._challenge_event_map)} "
          f"subevent_map={dict(d._challenge_subevent_map)}")

    # Expected correct board for a Germany stage / round-2 screen:
    # Agersi alone with 182.274s (Germany stage 0 only).

    show("ROUND 2 / stage-level id  base*10+1_000_000+0  (PB=182274)",
         d.dispatch("RaceNetLeaderboard.GetLeaderboardEntries",
                    {"LeaderboardId": Int64(base_a * 10 + 1_000_000 + 0),
                     "PlayerBest": Int64(182274)}), client)

    show("ROUND 2 / challenge-level id  (base+1)+800000",
         d.dispatch("RaceNetLeaderboard.GetLeaderboardEntries",
                    {"LeaderboardId": Int64((base_a + 1) + 800000),
                     "PlayerBest": Int64(0)}), client)

    ev_lb = evs[0].get("LeaderboardId") if evs else None
    print(f"\nEvent.leaderboard_id served to game: {ev_lb}")
    show("ROUND 2 / event-level id  base+900000+10_000_000",
         d.dispatch("RaceNetLeaderboard.GetLeaderboardEntries",
                    {"LeaderboardId": Int64(ev_lb),
                     "PlayerBest": Int64(0)}), client)

    show("ROUND 1 / event-level id  base+900000  (why round 1 looked fine)",
         d.dispatch("RaceNetLeaderboard.GetLeaderboardEntries",
                    {"LeaderboardId": Int64(base_a + 900000),
                     "PlayerBest": Int64(0)}), client)


if __name__ == "__main__":
    main()
