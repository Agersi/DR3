r"""Verify the #39 gated experiment: a Multi-Location Rally championship served
as ONE challenge carrying every leg in Events[] (the upstream official
monthly-challenge shape), so the game can stay on the event screen between
legs.  Gated by the event setting ``settings.single_challenge`` (default off,
multi mode only) — with the flag off the per-leg model is byte-identical to
before.

Unit-level checks against RpcDispatcher:
  - GetClubs serving: one challenge at the base id with all legs in Events[],
    Club AmountOfEvents=N / EventIndex=active, Progress keyed to the base id
    with EventIndex=active; flag off and season mode keep the per-leg shape;
  - StageBegin on a later leg (EventIndex=leg): sub-event recovered from the
    request, flat ordinal offset applied, response echoes EventIndex;
  - StageComplete: leg completion judged against THIS leg's stage count,
    championship-wide ChampTimeMs, response echoes EventIndex;
  - GetChallengeRecap: one entry per (EventIndex, StageIndex) for EVERY leg
    with the running championship total.

Run: python tools\repro_single_challenge.py   (from D:\CODE\DR2)
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore  # noqa: E402
from dr2server.api_client import (  # noqa: E402
    _default_tracks_for_location, _location_id_for, _vclass_id_for)
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

    def __init__(self, events=None, progress_events=None):
        self.events = events or {}
        self.progress = {"events": progress_events or []}
        self.begin_calls = []

    def resolve_location_id(self, name):
        return _location_id_for(name)

    def resolve_vclass_id(self, label):
        return _vclass_id_for(label)

    def tracks_for_location(self, loc_id):
        return _default_tracks_for_location(loc_id)

    def get_event(self, event_id):
        return self.events.get(event_id)

    def get_my_progress(self):
        return self.progress

    def test_token(self):
        return "Agersi"

    def submit_stage_begin(self, **kw):
        self.begin_calls.append(kw)

    def submit_stage(self, **kw):
        return {"ok": True}


def make_dispatcher(api):
    store = AccountStore(Path(r"D:\CODE\DR2\build\single_challenge_test_accounts") / "u")
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
        {"location": "Monte Carlo", "car_class": "Group A",
         "stages": [{"name": f"leg0-s{i}"} for i in range(2)]},
        {"location": "Monte Carlo", "car_class": "Group A",
         "stages": [{"name": f"leg1-s{i}"} for i in range(3)]},
    ]


def event_with(flag, mode="multi"):
    settings = {"mode": mode}
    if flag is not None:
        settings["single_challenge"] = flag
    return {"id": "evtS", "name": "Single Challenge Test", "mode": mode,
            "car_class": "Group A", "events": _legs(), "settings": settings}


# Leg 0 fully done (5000 + 6000); leg 1 not started.
EP_LEG0_DONE = [stage(0, 5000), stage(1, 6000)]
# Leg 0 done + leg 1 stage 0 done (7000).
EP_THRU_LEG1_S0 = EP_LEG0_DONE + [stage(2, 7000)]


def progress_for(stages):
    return {"event_id": "evtS", "completed_stages": stages, "attempts_used": 0}


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


def sb(stage_index, chal, event_index=0):
    p = dict(SB_PARAMS)
    p["StageIndex"] = stage_index
    p["EventIndex"] = event_index
    p["ChallengeId"] = chal
    return p


def sc(stage_index, chal, event_index=0):
    p = dict(SC_PARAMS)
    p["StageIndex"] = stage_index
    p["EventIndex"] = event_index
    p["ChallengeId"] = chal
    return p


def main():
    base = _stable_int_id("evtS", base=200000)

    # ------------------------------------------- GetClubs, flag ON (multi mode)
    api = FakeApi(events={"evtS": event_with(True)},
                  progress_events=[progress_for(EP_LEG0_DONE)])
    d = make_dispatcher(api)
    res = d._serve_multi_event_club(event_with(True), "club-1", 5001,
                                    "Test Club", "Agersi", [2, 3])
    check("flag ON: served", res is not None, True)
    if res:
        club, chal, prog = res
        check("flag ON: challenge id is the base id", unwrap(chal["ChallengeID"]), base)
        check("flag ON: both legs in Events[]", len(chal["Events"]), 2)
        check("flag ON: leg0 EventId", unwrap(chal["Events"][0]["EventId"]), base)
        check("flag ON: leg1 EventId",
              unwrap(chal["Events"][1]["EventId"]), base + 10_000_000)
        check("flag ON: leg stage counts",
              [len(e["Stages"]) for e in chal["Events"]], [2, 3])
        check("flag ON: Club AmountOfEvents", club["AmountOfEvents"], 2)
        check("flag ON: Club EventIndex (active leg)", club["EventIndex"], 1)
        check("flag ON: progress keyed to base id", unwrap(prog["ChallengeID"]), base)
        check("flag ON: progress EventIndex points at active leg",
              prog["EventIndex"], 1)
        check("flag ON: seeded next-leg State", unwrap(prog["State"]), 1)
        check("flag ON: seeded next-leg ChampTimeMs (championship total)",
              unwrap(prog["ChampTimeMs"]), 11000)
        check("flag ON: carried vehicle",
              (unwrap(prog["VehicleId"]), unwrap(prog["LiveryId"])), (469, 3233))
    check("flag ON: base id in _multi_event_challenges",
          base in d._multi_event_challenges, True)
    check("flag ON: per-leg id NOT registered",
          (base + 1) in d._challenge_subevent_map, False)
    check("flag ON: is_local_challenge on the base id",
          d.is_local_challenge({"ChallengeId": base}), True)

    # ------------------------------------------- GetClubs, flag OFF (per-leg)
    api = FakeApi(events={"evtS": event_with(False)},
                  progress_events=[progress_for(EP_LEG0_DONE)])
    d = make_dispatcher(api)
    res = d._serve_multi_event_club(event_with(False), "club-1", 5001,
                                    "Test Club", "Agersi", [2, 3])
    check("flag OFF: served", res is not None, True)
    if res:
        club, chal, prog = res
        check("flag OFF: challenge id is base+active (per-leg)",
              unwrap(chal["ChallengeID"]), base + 1)
        check("flag OFF: single leg in Events[]", len(chal["Events"]), 1)
        check("flag OFF: progress EventIndex stays 0", prog["EventIndex"], 0)
    check("flag OFF: base id NOT in _multi_event_challenges",
          base in d._multi_event_challenges, False)

    # ------------------------------------------- GetClubs, flag ON + season mode
    api = FakeApi(events={"evtS": event_with(True, mode="season")},
                  progress_events=[progress_for(EP_LEG0_DONE)])
    d = make_dispatcher(api)
    res = d._serve_multi_event_club(event_with(True, mode="season"), "club-1",
                                    5001, "Test Club", "Agersi", [2, 3])
    check("season + flag ON: served (per-leg fallback)", res is not None, True)
    if res:
        _club, chal, prog = res
        check("season + flag ON: per-leg challenge id",
              unwrap(chal["ChallengeID"]), base)  # active=0 -> base+0
        check("season + flag ON: single leg in Events[]", len(chal["Events"]), 1)
        check("season + flag ON: progress EventIndex stays 0", prog["EventIndex"], 0)
    check("season + flag ON: base id NOT in _multi_event_challenges",
          base in d._multi_event_challenges, False)

    # ------------------------------------------------- StageBegin on leg 1
    api = FakeApi(events={"evtS": event_with(True)},
                  progress_events=[progress_for(EP_THRU_LEG1_S0)])
    d = make_dispatcher(api)
    d._challenge_event_map[base] = "evtS"
    d._multi_event_challenges.add(base)
    r = d._stage_begin(sb(0, base, event_index=1))
    check("SB leg1: ok", r["ok"], True)
    check("SB leg1: response EventIndex echoes the leg",
          r["Progress"]["EventIndex"], 1)
    check("SB leg1: ChampTimeMs is the championship total so far",
          unwrap(r["Progress"]["ChampTimeMs"]), 11000)
    check("SB leg1: submit_stage_begin carries event_index=1",
          [(c.get("stage_index"), c.get("event_index")) for c in api.begin_calls],
          [(0, 1)])

    # --------------------------------- StageComplete: final stage of leg 0
    api = FakeApi(events={"evtS": event_with(True)},
                  progress_events=[progress_for(EP_LEG0_DONE)])
    d = make_dispatcher(api)
    d._challenge_event_map[base] = "evtS"
    d._multi_event_challenges.add(base)
    r = d._stage_complete(sc(1, base, event_index=0))
    check("SC leg0 final: State=2 (leg finished)", unwrap(r["Progress"]["State"]), 2)
    check("SC leg0 final: AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 0)
    check("SC leg0 final: response EventIndex echoes the leg",
          r["Progress"]["EventIndex"], 0)
    check("SC leg0 final: ChampTimeMs is the championship total",
          unwrap(r["Progress"]["ChampTimeMs"]), 11000)

    # --------------------------------- StageComplete: intermediate stage of leg 1
    api = FakeApi(events={"evtS": event_with(True)},
                  progress_events=[progress_for(EP_LEG0_DONE)])
    d = make_dispatcher(api)
    d._challenge_event_map[base] = "evtS"
    d._multi_event_challenges.add(base)
    r = d._stage_complete(sc(0, base, event_index=1))
    check("SC leg1 s0: State=0 (mid-leg)", unwrap(r["Progress"]["State"]), 0)
    check("SC leg1 s0: next stage offered",
          r["Progress"]["StageIndex"], 1)
    check("SC leg1 s0: response EventIndex echoes the leg",
          r["Progress"]["EventIndex"], 1)
    # Championship total (leg 0 done) + this stage's time (web hasn't seen it).
    check("SC leg1 s0: ChampTimeMs = 11000 + 100500",
          unwrap(r["Progress"]["ChampTimeMs"]), 111500)

    # ------------------------------------------------- Recap: every leg listed
    api = FakeApi(events={"evtS": event_with(True)},
                  progress_events=[progress_for(EP_THRU_LEG1_S0)])
    d = make_dispatcher(api)
    d._challenge_event_map[base] = "evtS"
    d._multi_event_challenges.add(base)
    r = d._challenge_recap({"ChallengeId": base})
    got = [(e["EventIndex"], e["StageIndex"], e["State"],
            unwrap(e["StageTimeMs"]), unwrap(e["CumulTimeMs"]))
           for e in r["Recap"]]
    check("recap: one entry per (leg, stage) of EVERY leg",
          got,
          [(0, 0, 2, 5000, 5000),
           (0, 1, 2, 6000, 11000),
           (1, 0, 2, 7000, 18000),
           (1, 1, 0, 0, 18000),
           (1, 2, 0, 0, 18000)])

    # Recap negative control: a per-leg id still recaps only its own leg.
    d._challenge_event_map[base + 1] = "evtS"
    d._challenge_subevent_map[base + 1] = 1
    r = d._challenge_recap({"ChallengeId": base + 1})
    got = [(e["EventIndex"], e["StageIndex"], e["State"]) for e in r["Recap"]]
    check("recap: per-leg id keeps the single-leg shape",
          got, [(0, 0, 2), (0, 1, 0), (0, 2, 0)])

    print()
    if FAILS:
        print(f"{FAILS} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
