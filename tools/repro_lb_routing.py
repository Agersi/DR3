"""Verify backlog #16 fix — leaderboard requests for local championship ids
must be handled by the local dispatcher, not proxied to the real EGO server.

Mirrors the user's 2026-10-24 session (captures in
C:\\Users\\Agersi\\AppData\\Roaming\\DR3\\captures): every
RaceNetLeaderboard.GetLeaderboardEntries call for a club-championship id was
proxied upstream because the method was not in CHALLENGE_ROUTED_METHODS, so
rounds 1+3 of the Multi-Location Test showed the upstream server's data for
those ids.

Checks:
  1. dispatcher.is_local_leaderboard() -> True for every captured club id,
     False for real upstream daily-challenge ids.
  2. App._should_proxy() in default proxy mode (upstream_ip set) routes the
     same ids accordingly.

Run: python tools\\repro_lb_routing.py   (from D:\\CODE\\DR2)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.dispatcher import RpcDispatcher  # noqa: E402
from dr2server.models import Int64  # noqa: E402
from dr2server.game_data import Location, Track, VehicleClass  # noqa: E402
from dr2server.httpd import App, RequestHandler  # noqa: E402

# Live VPS events (id -> base computed by _stable_int_id(id, base=200000)).
EVENTS = [
    ("evt-60454014", "Multi-Location Test", 3),   # base 251893
    ("evt-de4aeb72", "Rally Season", 3),          # base 219365
    ("evt-c27fa19d", "No Deg", 5),                # base 233018
    ("evt-32aceb25", "Just a test", 2),           # base 275619
    ("evt-a4b4f489", "RX Test", 2),               # base 258671
]

# Captured in the user's session — all must route LOCAL.
LOCAL_IDS = [
    2518930, 4518930,            # Multi-Location Test stage boards (legs 0, 2)
    1051895,                      #   challenge-level, leg 2 active
    2193650, 2193651, 2193652,    # Rally Season stage boards
    1019365,                      #   challenge-level, leg 0
    2330180, 2330181, 2330182,    # No Deg stage boards
    2330183, 2330184,
    1033018,                      #   challenge-level, leg 0
    1075620,                      # Just a test challenge-level, leg 1
    1151893, 1151893 + 10_000_000,  # Multi-Location Test event-level ids
]

# Real upstream daily-challenge boards seen in the same session — must stay
# PROXIED so the game keeps showing real RaceNet data.
UPSTREAM_IDS = [6875391, 6875392, 6875338, 6875388, 6875116, 6875117, 6875125]


def _stage(track):
    return {"track_id": int(track), "conditions_id": 1,
            "surface_deg": None, "service_area": None}


def _event(eid, name, legs):
    return {
        "id": eid, "club_id": f"club-{eid[-6:]}", "name": name,
        "active": True, "car_class": "Group B 4WD", "mode": "multi",
        "settings": {},
        "start_time": "2026-10-01T00:00:00", "end_time": "2027-01-01T00:00:00",
        "events": [
            {"location": "Poland" if i % 2 == 0 else "Germany",
             "car_class": "Group B 4WD",
             "stages": [_stage(Track.JEZIORO_ROTCZE), _stage(Track.MARYNKA)]}
            for i in range(legs)
        ],
    }


class FakeClient:
    def get_clubs(self):
        return {
            "clubs": [{"id": f"club-{eid[-6:]}", "name": name,
                       "created_by": "Agersi"} for eid, name, _ in EVENTS],
            "events": [_event(eid, name, legs) for eid, name, legs in EVENTS],
        }

    def resolve_location_id(self, name):
        return int(getattr(Location, name.upper()))

    def tracks_for_location(self, lid):
        if lid == int(Location.POLAND):
            return [int(Track.JEZIORO_ROTCZE), int(Track.MARYNKA)]
        return [int(t) for t in (Track.VERBUNDSRING_REVERSE,
                                 Track.INNERER_FELD_SPRINT_UMGEKEHRT,
                                 Track.RUSCHBERG)]

    def resolve_vclass_id(self, label):
        return int(VehicleClass.GROUP_B_4WD)

    def get_event(self, eid):
        for e in EVENTS:
            if e[0] == eid:
                return _event(*e)
        return None

    def get_leaderboard(self, eid):
        return []

    def get_my_progress(self):
        return {"ok": True, "events": []}

    def test_token(self):
        return "Agersi"


def main():
    d = RpcDispatcher(account_store=None, api_client=FakeClient())
    d.dispatch("Clubs.GetClubs", {})

    from dr2server.dispatcher import _stable_int_id
    bases = {eid: _stable_int_id(eid, base=200000) for eid, _, _ in EVENTS}
    print(f"computed bases: {bases}")
    expected = {"evt-60454014": 251893, "evt-de4aeb72": 219365,
                "evt-c27fa19d": 233018, "evt-32aceb25": 275619,
                "evt-a4b4f489": 258671}
    assert bases == expected, f"base mismatch: {bases}"

    # Mirror _serve_multi_event_club: the served (active) leg registers
    # base+k with its sub-event index. Seed every leg so all captured ids —
    # including challenge-level ids for legs 1+ — are in the map, exactly as
    # they are when the game actually queries them in-rally.
    for eid, _, legs in EVENTS:
        for k in range(legs):
            d._challenge_event_map[bases[eid] + k] = eid
            d._challenge_subevent_map[bases[eid] + k] = k

    fails = 0
    print("\n-- dispatcher.is_local_leaderboard --")
    for lb in LOCAL_IDS:
        got = d.is_local_leaderboard({"LeaderboardId": Int64(lb)})
        mark = "ok " if got else "FAIL"
        if not got:
            fails += 1
        print(f"  {mark} local   {lb}")
    for lb in UPSTREAM_IDS:
        got = d.is_local_leaderboard({"LeaderboardId": Int64(lb)})
        mark = "ok " if not got else "FAIL"
        if got:
            fails += 1
        print(f"  {mark} proxy   {lb}")

    # App-level routing in default proxy mode (what DR3.exe runs).
    scratch = ROOT / "build" / "lb_routing_scratch"
    app = App(data_root=scratch, capture_root=scratch / "captures",
              upstream_ip="159.153.126.42", proxy_default_except_clubs=True)
    for eid, _, legs in EVENTS:
        for k in range(legs):
            app.dispatcher._challenge_event_map[bases[eid] + k] = eid
            app.dispatcher._challenge_subevent_map[bases[eid] + k] = k

    print("\n-- RequestHandler._should_proxy (default proxy mode) --")
    # Unbound handler instance — _should_proxy only touches self.app, which
    # is a property delegating to self.server.app.
    import types
    handler = RequestHandler.__new__(RequestHandler)
    handler.server = types.SimpleNamespace(app=app)
    method = "RaceNetLeaderboard.GetLeaderboardEntries"
    for lb in LOCAL_IDS:
        got = handler._should_proxy(method, {"LeaderboardId": Int64(lb)})
        mark = "ok " if not got else "FAIL"
        if got:
            fails += 1
        print(f"  {mark} local   {lb}")
    for lb in UPSTREAM_IDS:
        got = handler._should_proxy(method, {"LeaderboardId": Int64(lb)})
        mark = "ok " if got else "FAIL"
        if not got:
            fails += 1
        print(f"  {mark} proxy   {lb}")

    # Non-leaderboard methods keep their existing behaviour.
    assert handler._should_proxy("RaceNetChallenges.GetChallenges", {}) is True
    assert handler._should_proxy("Clubs.GetClubs", {}) is False
    assert handler._should_proxy(
        "RaceNetChallenges.StageBegin", {"ChallengeId": Int64(bases["evt-32aceb25"])}) is False

    print(f"\n{'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
