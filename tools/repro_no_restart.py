r"""Verify items #19 + #21 fixes: club championship legs are never restartable.

Unit-level checks against RpcDispatcher:
  #21 — Event.NumberRestarts is 0 for both rally and RX sub-events (upstream
        RaceNet's own club events carry 0; RX_NUMBER_RESTARTS=5 no longer sent).
  #19 — AttemptsLeft is 0 on fully completed legs (GetClubs progress, stage
        begin/complete responses) so the client has no Start/Restart button;
        1 while the leg is still in progress.

Run: python tools\repro_no_restart.py   (from D:\CODE\DR2)
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore
from dr2server.dispatcher import RpcDispatcher
from dr2server.game_data import Location, Track

FAILS = 0


def unwrap(v):
    return v.value if hasattr(v, "value") else v


def check(label, got, want):
    global FAILS
    ok = got == want
    print(f"[{'OK' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS += 1


class FakeApi:
    """Just enough of UpstreamClient for the code paths under test."""

    def __init__(self, events=None, progress_events=None):
        def find_loc(pred):
            for i in range(1, 80):
                try:
                    loc = Location(i)
                except ValueError:
                    continue
                if pred(loc):
                    return i
            raise AssertionError("no matching location found")

        self.rx_loc = find_loc(lambda l: l.discipline == "rallycross")
        self.rally_loc = find_loc(lambda l: l.discipline != "rallycross")

        def first_track(loc_id):
            for t in range(1, 3000):
                try:
                    if Track(t).location == loc_id:
                        return t
                except ValueError:
                    continue
            raise AssertionError(f"no track for location {loc_id}")

        self._tracks = {self.rx_loc: [first_track(self.rx_loc)],
                        self.rally_loc: [first_track(self.rally_loc)]}
        self.events = events or {}
        self.progress = {"events": progress_events or []}

    def resolve_location_id(self, name):
        return {"rx": self.rx_loc, "rally": self.rally_loc}.get(name)

    def tracks_for_location(self, loc_id):
        return self._tracks.get(loc_id, [])

    def get_my_progress(self):
        return self.progress

    def get_event(self, event_id):
        return self.events.get(event_id)

    def submit_stage_begin(self, **kw):
        pass

    def submit_stage(self, **kw):
        return {"ok": True}


def make_dispatcher(api=None):
    store = AccountStore(Path(r"D:\CODE\DR2\build\no_restart_test_accounts") / "u")
    d = RpcDispatcher(store, api_client=api or FakeApi())
    return d


def stage(idx, time_ms=1000):
    return {
        "stage_index": idx,
        "time_ms": time_ms,
        "meters_driven": 500 + idx,
        "vehicle_id": 469,
        "livery_id": 3233,
        "has_repaired": False,
        "repair_penalty_ms": 0,
        "vehicle_damage": {"bodywork": 12, "engine": 3.5},
        "tyre_compound": 6,
        "tyres_remaining": 1,
        "tuning_setup_b64": "",
    }


SB_PARAMS = {
    "VehicleId": 469, "VehicleInstId": -4, "LiveryId": 3233,
    "StageIndex": 0, "EventIndex": 0,
    "TuningSetup": {"blob_base64": "", "size": 0},
    "ChallengeId": 0, "NationalityId": 31,
    "TyresRemaining": 2, "TyreCompound": 7,
}


def sb(stage_index, chal):
    p = dict(SB_PARAMS)
    p["StageIndex"] = stage_index
    p["ChallengeId"] = chal
    return p


SC_PARAMS = {
    "VehicleId": 469, "VehicleInstId": -4, "LiveryId": 3233,
    "StageIndex": 0, "EventIndex": 0, "ChallengeId": 0,
    "NationalityId": 31, "StageTime": 100.5,
    "MetersDriven": 1234, "DistanceDriven": 1234,
    "RaceStatus": 0, "RecovToService": False,
}


def sc(stage_index, chal):
    p = dict(SC_PARAMS)
    p["StageIndex"] = stage_index
    p["ChallengeId"] = chal
    return p


def main():
    # ------------------------------------------------------------------ #
    # #21 — NumberRestarts=0 on every club sub-event (rally and RX).     #
    # ------------------------------------------------------------------ #
    d = make_dispatcher()
    ev_rx = d._build_subevent({"id": "x"}, 123456, 0, {"location": "rx", "stages": [{}]})
    check("#21 RX sub-event built", ev_rx is not None, True)
    if ev_rx:
        check("#21 RX NumberRestarts", unwrap(ev_rx.to_egonet()["NumberRestarts"]), 0)
    ev_rally = d._build_subevent({"id": "x"}, 123456, 0, {"location": "rally", "stages": [{}]})
    check("#21 rally sub-event built", ev_rally is not None, True)
    if ev_rally:
        check("#21 rally NumberRestarts", unwrap(ev_rally.to_egonet()["NumberRestarts"]), 0)

    # ------------------------------------------------------------------ #
    # #19 — AttemptsLeft=0 on completed legs, 1 while in progress.       #
    # ------------------------------------------------------------------ #
    ep_full = {"completed_stages": [stage(0), stage(1), stage(2)], "attempts_used": 0}
    ep_part = {"completed_stages": [stage(0)], "attempts_used": 0}

    check("#19 _attempts_left_for in-progress", d._attempts_left_for(ep_part), 1)
    check("#19 _attempts_left_for completed", d._attempts_left_for(ep_full, completed=True), 0)
    check("#19 _attempts_left_for None ep", d._attempts_left_for(None), 1)

    # Multi-event progress: finished leg -> State=2 + AttemptsLeft=0.
    p = d._multi_event_progress(999, [3], 0, ep_full, carry_over=False)
    check("#19 GetClubs finished leg State", unwrap(p["State"]), 2)
    check("#19 GetClubs finished leg AttemptsLeft", unwrap(p["AttemptsLeft"]), 0)

    # Multi-event progress: partial leg -> State=1 + AttemptsLeft=1.
    p = d._multi_event_progress(999, [3], 0, ep_part, carry_over=False)
    check("#19 GetClubs partial leg State", unwrap(p["State"]), 1)
    check("#19 GetClubs partial leg AttemptsLeft", unwrap(p["AttemptsLeft"]), 1)

    # Multi-event seed (carry-over into an unstarted leg): still enterable.
    p = d._multi_event_progress(999, [1, 1], 1, ep_part, carry_over=True)
    check("#19 GetClubs seeded next leg State", unwrap(p["State"]), 1)
    check("#19 GetClubs seeded next leg AttemptsLeft", unwrap(p["AttemptsLeft"]), 1)

    # _build_user_progress (the Clubs.GetClubs Progress list).
    d2 = make_dispatcher(FakeApi(progress_events=[
        {"event_id": "evtA", "completed_stages": [stage(0), stage(1), stage(2)],
         "total_time_ms": 3000, "attempts_used": 0},
        {"event_id": "evtB", "completed_stages": [stage(0)],
         "total_time_ms": 1000, "attempts_used": 0},
    ]))
    d2._challenge_event_map = {700001: "evtA", 700002: "evtB"}
    prog = d2._build_user_progress([
        {"id": "evtA", "stages": [stage(0), stage(1), stage(2)]},
        {"id": "evtB", "stages": [stage(0), stage(1), stage(2)]},
    ])
    by_chal = {unwrap(x["ChallengeID"]): x for x in prog}
    check("#19 GetClubs progress entry count", len(prog), 2)
    check("#19 GetClubs evtA (done) State", unwrap(by_chal[700001]["State"]), 2)
    check("#19 GetClubs evtA (done) AttemptsLeft", unwrap(by_chal[700001]["AttemptsLeft"]), 0)
    check("#19 GetClubs evtB (partial) State", unwrap(by_chal[700002]["State"]), 1)
    check("#19 GetClubs evtB (partial) AttemptsLeft", unwrap(by_chal[700002]["AttemptsLeft"]), 1)

    # StageBegin: fresh start and mid-leg continue keep AttemptsLeft=1; a
    # re-begin of an already-finished event reports 0.
    CHAL = 800001
    d3 = make_dispatcher(FakeApi(
        events={"evtC": {"stages": [stage(0), stage(1), stage(2)]}},
        progress_events=[
            {"event_id": "evtC", "completed_stages": [stage(0)],
             "total_time_ms": 1000, "attempts_used": 0},
        ],
    ))
    d3._challenge_event_map = {CHAL: "evtC"}
    d3._challenge_subevent_map = {CHAL: 0}

    r = d3._stage_begin(sb(1, CHAL))
    check("#19 SB mid-leg State", unwrap(r["Progress"]["State"]), 1)
    check("#19 SB mid-leg AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 1)

    d4 = make_dispatcher(FakeApi(
        events={"evtD": {"stages": [stage(0), stage(1), stage(2)]}},
        progress_events=[
            {"event_id": "evtD", "completed_stages": [stage(0), stage(1), stage(2)],
             "total_time_ms": 3000, "attempts_used": 0},
        ],
    ))
    d4._challenge_event_map = {CHAL: "evtD"}
    d4._challenge_subevent_map = {CHAL: 0}
    r = d4._stage_begin(sb(0, CHAL))  # restart attempt on a finished event
    check("#19 SB restart-attempt AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 0)

    # StageComplete: finishing the last stage -> State=2 + AttemptsLeft=0;
    # an intermediate finish -> State=0 + AttemptsLeft=1.
    d5 = make_dispatcher(FakeApi(
        events={"evtE": {"stages": [stage(0), stage(1), stage(2)]}},
        progress_events=[
            {"event_id": "evtE", "completed_stages": [stage(0), stage(1)],
             "total_time_ms": 2000, "attempts_used": 0},
        ],
    ))
    d5._challenge_event_map = {CHAL: "evtE"}
    d5._challenge_subevent_map = {CHAL: 0}
    r = d5._stage_complete(sc(1, CHAL))
    check("#19 SC intermediate State", unwrap(r["Progress"]["State"]), 0)
    check("#19 SC intermediate AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 1)

    d6 = make_dispatcher(FakeApi(
        events={"evtF": {"stages": [stage(0), stage(1), stage(2)]}},
        progress_events=[
            {"event_id": "evtF", "completed_stages": [stage(0), stage(1)],
             "total_time_ms": 2000, "attempts_used": 0},
        ],
    ))
    d6._challenge_event_map = {CHAL: "evtF"}
    d6._challenge_subevent_map = {CHAL: 0}
    r = d6._stage_complete(sc(2, CHAL))
    check("#19 SC final State", unwrap(r["Progress"]["State"]), 2)
    check("#19 SC final AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 0)

    print()
    if FAILS:
        print(f"{FAILS} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
