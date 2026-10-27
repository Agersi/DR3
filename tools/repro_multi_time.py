r"""Verify backlog #38: Multi-Location Rally shows championship-wide times.

Each leg of a club championship is served as its own single-event challenge,
so before this fix every timer (StageBegin/StageComplete ChampTimeMs, the
Event Progress recap cumulative, and the Event Standings leaderboard totals)
restarted at zero per leg. Multi-Location Rally now sums the whole
championship; Rally Season keeps per-leg timers.

Unit-level checks against RpcDispatcher:
  - StageBegin / StageComplete ChampTimeMs: championship total in multi mode,
    per-rally in season mode;
  - GetChallengeRecap cumulative seeding (see repro_challenge_recap.py);
  - _multi_event_progress (GetClubs entry) championship-wide time, both the
    active-leg and carry-over seed branches;
  - leaderboard: multi boards sum from flat stage 0 capped at the player's
    overall max completed stage; season boards keep the per-rally range.

Run: python tools\repro_multi_time.py   (from D:\CODE\DR2)
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore  # noqa: E402
from dr2server.dispatcher import RpcDispatcher, _stable_int_id  # noqa: E402

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

    def __init__(self, events=None, progress_events=None, lb_entries=None):
        self.events = events or {}
        self.progress = {"events": progress_events or []}
        self.lb_entries = lb_entries or []

    def get_event(self, event_id):
        return self.events.get(event_id)

    def get_my_progress(self):
        return self.progress

    def get_leaderboard(self, event_id):
        return self.lb_entries

    def test_token(self):
        return "Agersi"

    def submit_stage_begin(self, **kw):
        pass

    def submit_stage(self, **kw):
        return {"ok": True}


def make_dispatcher(api):
    store = AccountStore(Path(r"D:\CODE\DR2\build\multi_time_test_accounts") / "u")
    return RpcDispatcher(store, api_client=api)


def stage(idx, time_ms):
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


# Championship layout: leg0 = 2 stages (gidx 0,1), leg1 = 3 stages (gidx 2..4).
def _legs():
    return [
        {"stages": [{"name": f"leg0-s{i}"} for i in range(2)]},
        {"stages": [{"name": f"leg1-s{i}"} for i in range(3)]},
    ]


EVENT_MULTI = {"events": _legs(), "settings": {"mode": "multi"}}
EVENT_SEASON = {"events": _legs(), "settings": {"mode": "season"}}

# Leg 0 fully done (5000 + 6000), leg 1 stage 0 done (7000).
PROGRESS_THROUGH_LEG1_S0 = {
    "event_id": None,  # patched per event below
    "completed_stages": [stage(0, 5000), stage(1, 6000), stage(2, 7000)],
    "attempts_used": 0,
}
# Before leg 1 starts: only leg 0's stages are persisted.
PROGRESS_THROUGH_LEG0 = {
    "event_id": None,
    "completed_stages": [stage(0, 5000), stage(1, 6000)],
    "attempts_used": 0,
}


def progress_for(event_id, template):
    ep = dict(template)
    ep["event_id"] = event_id
    return ep


SB_PARAMS = {
    "VehicleId": 469, "VehicleInstId": -4, "LiveryId": 3233,
    "StageIndex": 0, "EventIndex": 0,
    "TuningSetup": {"blob_base64": "", "size": 0},
    "ChallengeId": 0, "NationalityId": 31,
    "TyresRemaining": 2, "TyreCompound": 7,
}

SC_PARAMS = {
    "VehicleId": 469, "VehicleInstId": -4, "LiveryId": 3233,
    "StageIndex": 0, "EventIndex": 0, "ChallengeId": 0,
    "NationalityId": 31, "StageTime": 100.5,
    "MetersDriven": 1234, "DistanceDriven": 1234,
    "RaceStatus": 0, "RecovToService": False,
}


def sb(stage_index, chal):
    p = dict(SB_PARAMS)
    p["StageIndex"] = stage_index
    p["ChallengeId"] = chal
    return p


def sc(stage_index, chal):
    p = dict(SC_PARAMS)
    p["StageIndex"] = stage_index
    p["ChallengeId"] = chal
    return p


def main():
    # ------------------------------------------------- StageBegin ChampTimeMs
    for mode_key, event, want in (
        ("multi", EVENT_MULTI, 11000),     # leg0 total carried into leg 1
        ("season", EVENT_SEASON, 0),       # per-leg timer restarts at 0
    ):
        api = FakeApi(
            events={"evtT": event},
            progress_events=[progress_for("evtT", PROGRESS_THROUGH_LEG1_S0)],
        )
        d = make_dispatcher(api)
        base = _stable_int_id("evtT", base=200000)
        chal = base + 1  # leg 1 challenge
        d._challenge_event_map[chal] = "evtT"
        d._challenge_subevent_map[chal] = 1
        r = d._stage_begin(sb(0, chal))
        check(f"{mode_key}: SB leg1 stage0 ChampTimeMs",
              unwrap(r["Progress"]["ChampTimeMs"]), want)

    # ----------------------------------------------- StageComplete ChampTimeMs
    for mode_key, event, want in (
        ("multi", EVENT_MULTI, 11000 + 100500),   # champ total + this stage
        ("season", EVENT_SEASON, 100500),         # this rally's stages only
    ):
        api = FakeApi(
            events={"evtT": event},
            progress_events=[progress_for("evtT", PROGRESS_THROUGH_LEG0)],
        )
        d = make_dispatcher(api)
        base = _stable_int_id("evtT", base=200000)
        chal = base + 1
        d._challenge_event_map[chal] = "evtT"
        d._challenge_subevent_map[chal] = 1
        r = d._stage_complete(sc(0, chal))
        check(f"{mode_key}: SC leg1 stage0 ChampTimeMs",
              unwrap(r["Progress"]["ChampTimeMs"]), want)

    # --------------------------------------------- _multi_event_progress (GetClubs)
    ep_full_legs = progress_for("evtT", PROGRESS_THROUGH_LEG1_S0)
    d = make_dispatcher(FakeApi())

    p = d._multi_event_progress(999, [2, 3], 1, ep_full_legs,
                                carry_over=True, championship_time=True)
    check("multi: GetClubs active leg1 ChampTimeMs (championship total)",
          unwrap(p["ChampTimeMs"]), 18000)
    p = d._multi_event_progress(999, [2, 3], 1, ep_full_legs,
                                carry_over=False, championship_time=False)
    check("season: GetClubs active leg1 ChampTimeMs (per-leg)",
          unwrap(p["ChampTimeMs"]), 7000)

    p = d._multi_event_progress(999, [2, 3, 1], 2, ep_full_legs,
                                carry_over=True, championship_time=True)
    check("multi: GetClubs seeded next-leg ChampTimeMs (championship total)",
          unwrap(p["ChampTimeMs"]), 18000)
    p = d._multi_event_progress(999, [2, 3, 1], 2, ep_full_legs,
                                carry_over=True, championship_time=False)
    check("season: GetClubs seeded next-leg ChampTimeMs is 0",
          unwrap(p["ChampTimeMs"]), 0)

    # ---------------------------------------------------------------- leaderboard
    def flat(times):
        out = []
        for t in times:
            out.append({"stage_index": len(out), "time_ms": t} if t else None)
        return out

    entries_multi = [
        {"username": "Agersi", "vehicle_id": 469,
         "stages": flat([5000, 6000, 7000, 0, 0]), "total_time_ms": 18000},
        {"username": "Bob", "vehicle_id": 470,
         "stages": flat([4000, 5500, 9000, 8000, 7000]), "total_time_ms": 33500},
        {"username": "Carl", "vehicle_id": 471,
         "stages": flat([6000, 6000, 0, 0, 0]), "total_time_ms": 12000},
    ]
    api = FakeApi(
        events={"evtL": EVENT_MULTI},
        progress_events=[progress_for("evtL", PROGRESS_THROUGH_LEG1_S0)],
        lb_entries=entries_multi,
    )
    d = make_dispatcher(api)
    base = _stable_int_id("evtL", base=200000)
    d._challenge_event_map[base] = "evtL"
    d._challenge_subevent_map[base] = 0
    # Event-level board for leg 1 (base + 900000 + event_index*10_000_000).
    r = d._leaderboard({"LeaderboardId": base + 900000 + 1 * 10_000_000})
    got = [(e["Presence"]["Name"], unwrap(e["CumulativeBest"]))
           for e in r["Entries"]]
    # Capped at the player's overall max completed stage (gidx 2), summed from
    # flat stage 0: Agersi 5000+6000+7000=18000, Bob 4000+5500+9000=18500.
    # Carl is missing gidx 2 -> excluded. Ascending sort = fastest first.
    check("multi: LB leg1 board capped championship totals",
          got, [("Agersi", 18000), ("Bob", 18500)])

    # Season control: same progress/entries, per-rally range (gidx 2..4).
    api = FakeApi(
        events={"evtL": EVENT_SEASON},
        progress_events=[progress_for("evtL", PROGRESS_THROUGH_LEG1_S0)],
        lb_entries=entries_multi,
    )
    d = make_dispatcher(api)
    base = _stable_int_id("evtL", base=200000)
    d._challenge_event_map[base] = "evtL"
    d._challenge_subevent_map[base] = 0
    r = d._leaderboard({"LeaderboardId": base + 900000 + 1 * 10_000_000})
    got = [(e["Presence"]["Name"], unwrap(e["CumulativeBest"]))
           for e in r["Entries"]]
    # Per-leg: capped at gidx 2 (player's max inside leg 1), summed from the
    # leg's first stage only. Ascending sort = fastest first.
    check("season: LB leg1 board per-rally totals",
          got, [("Agersi", 7000), ("Bob", 9000)])

    print()
    if FAILS:
        print(f"{FAILS} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
