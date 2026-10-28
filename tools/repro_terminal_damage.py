r"""Verify backlog #32 step 2 — DNF semantics for terminal-damage stage close-outs.

Observed close-out shapes (captures/20261027-*, tools/stage_complete_audit.py):
  * normal finish:            RaceStatus=0, RecovToService=False   (all 13 observed)
  * club terminal damage:     RaceStatus=4, RecovToService=False
  * daily-shape close-out:    RaceStatus=0, RecovToService=True, StageTime = the
                              stage's time limit (900.0/1800.0s); the real upstream
                              answers that shape with State=6 DNF

Decision under test: only clean finishes are submitted as completions; both DNF
close-out shapes send a marker instead (mark_dnf=True, time_ms=0) so the web
side can flag the pre-persisted time-limit entry without changing its time —
web scoring then counts it as a DNF (zero rally points), and the bogus limit
value must not leak into the echoed ChampTimeMs.

Progress response: a DNF close-out ends the event/leg for this player, so the
response is ALWAYS State=2 (event finished) + AttemptsLeft=0 — never
"ready for next stage". Answering mid-leg after a wreck made the game start
the next stage with a destroyed car and crash on Continue (mid-leg recurrence
2026-10-28, access violation at dirtrally2.exe + 0x27d447; single-stage events
had only worked because the pre-persisted time-limit value already made the
stage count look done).

Marker payload: mid-leg abandons close out the stage AFTER the wrecked one
(captured 2026-10-28: wreck on stage 0 -> StageComplete(stage=1, rs=5)), so
the marker carries dnf_stage_index = stage_index - 1 and the web side flags
that entry too — otherwise its pre-persisted time-limit value scores as a
real finish.

Step 3 (service-area auto-advance): the marker also carries dnf_stages — one
entry per skipped stage, from the wreck up to the first stage after the next
service area, each with its max time from dr2server/stage_time_limits.csv
(RX rows are per-lap × NumberLaps). The web side flags every listed stage (and
fills in the limit where no pre-persisted value exists), so the count-based
progress resumes right after the last flagged stage.

Run: python tools\repro_terminal_damage.py   (from D:\CODE\DR2)
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore
from dr2server.api_client import _location_id_for
from dr2server.dispatcher import RpcDispatcher

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
    def __init__(self, events=None, progress_events=None):
        self.events = events or {}
        self.progress = {"events": progress_events or []}
        self.submitted = []

    def get_my_progress(self):
        return self.progress

    def get_event(self, event_id):
        return self.events.get(event_id)

    def resolve_location_id(self, location_name):
        return _location_id_for(location_name)

    def submit_stage_begin(self, **kw):
        pass

    def submit_stage(self, **kw):
        self.submitted.append(kw)
        return {"ok": True}


def make_dispatcher(api):
    store = AccountStore(Path(r"D:\CODE\DR2\build\terminal_damage_test_accounts") / "u")
    return RpcDispatcher(store, api_client=api)


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


SC_PARAMS = {
    "VehicleId": 469, "VehicleInstId": -4, "LiveryId": 3233,
    "StageIndex": 0, "EventIndex": 0, "ChallengeId": 0,
    "NationalityId": 31, "StageTime": 100.5,
    "MetersDriven": 1234, "DistanceDriven": 1234,
    "RaceStatus": 0, "RecovToService": False,
}


def sc(stage_index, chal=800001, race_status=0, recov=False, stage_time=100.5):
    p = dict(SC_PARAMS)
    p["StageIndex"] = stage_index
    p["ChallengeId"] = chal
    p["RaceStatus"] = race_status
    p["RecovToService"] = recov
    p["StageTime"] = stage_time
    return p


def fresh(chal=800001):
    """3-stage leg, one stage already completed (1000ms) on the web."""
    api = FakeApi(
        events={"evtT": {"stages": [stage(0), stage(1), stage(2)]}},
        progress_events=[
            {"event_id": "evtT", "completed_stages": [stage(0)],
             "total_time_ms": 1000, "attempts_used": 0},
        ],
    )
    d = make_dispatcher(api)
    d._challenge_event_map = {chal: "evtT"}
    d._challenge_subevent_map = {chal: 0}
    return d, api


def main():
    # Baseline: clean finish is submitted and its time joins ChampTimeMs.
    d, api = fresh()
    r = d._stage_complete(sc(1))
    check("clean: submit_stage called", len(api.submitted), 1)
    if api.submitted:
        check("clean: submitted time_ms", api.submitted[0]["time_ms"], 100500)
    check("clean: State (mid-leg)", unwrap(r["Progress"]["State"]), 0)
    check("clean: AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 1)
    check("clean: ChampTimeMs includes this stage",
          unwrap(r["Progress"]["ChampTimeMs"]), 1000 + 100500)

    # Club terminal-damage close-out (rs=4): a marker is sent (not a
    # completion), no phantom time.
    d, api = fresh()
    r = d._stage_complete(sc(1, race_status=4))
    check("rs=4: DNF marker sent", len(api.submitted), 1)
    if api.submitted:
        check("rs=4: marker flag", api.submitted[0].get("mark_dnf"), True)
        check("rs=4: marker time_ms is 0 (web keeps stored limit)",
              api.submitted[0]["time_ms"], 0)
        check("rs=4: marker race_status", api.submitted[0]["race_status"], 4)
        check("rs=4: marker points at the wrecked (previous) stage",
              api.submitted[0].get("dnf_stage_index"), 0)
        # Legacy-shape event (no service_area labels): parity fallback puts a
        # service area after stage 0, so only the wrecked stage is skipped.
        check("rs=4: skip plan = wrecked stage only (no limit known)",
              api.submitted[0].get("dnf_stages"),
              [{"stage_index": 0, "time_ms": 0}])
    check("rs=4: State (event finished after DNF)",
          unwrap(r["Progress"]["State"]), 2)
    check("rs=4: AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 0)
    check("rs=4: ChampTimeMs unchanged", unwrap(r["Progress"]["ChampTimeMs"]), 1000)

    # Daily-shape close-out (rs=0, recov=True, StageTime = stage time limit):
    # must be marked exactly like rs=4 — upstream answers this shape with
    # State=6 DNF, and the limit value is not a real stage time.
    d, api = fresh()
    r = d._stage_complete(sc(1, recov=True, stage_time=900.0))
    check("recov: DNF marker sent", len(api.submitted), 1)
    if api.submitted:
        check("recov: marker flag", api.submitted[0].get("mark_dnf"), True)
        check("recov: marker race_status normalised to 4",
              api.submitted[0]["race_status"], 4)
        check("recov: marker points at the wrecked (previous) stage",
              api.submitted[0].get("dnf_stage_index"), 0)
    check("recov: State (event finished after DNF)",
          unwrap(r["Progress"]["State"]), 2)
    check("recov: AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 0)
    check("recov: ChampTimeMs does not absorb the limit value",
          unwrap(r["Progress"]["ChampTimeMs"]), 1000)

    # Mid-leg abandon flow (observed 2026-10-28, evt-10872bf1 / HanBaoBao):
    # the game pre-persisted stage 0 with the time limit via its post-wreck
    # leaderboard query, then closed out the FOLLOWING stage retired (rs=5).
    # The marker lands on that next stage; the response must still end the
    # event, not offer the stage after it.
    api = FakeApi(
        events={"evtT": {"stages": [stage(0), stage(1), stage(2)]}},
        progress_events=[
            {"event_id": "evtT",
             "completed_stages": [stage(0, time_ms=900000)],
             "total_time_ms": 900000, "attempts_used": 0},
        ],
    )
    d = make_dispatcher(api)
    d._challenge_event_map = {800001: "evtT"}
    d._challenge_subevent_map = {800001: 0}
    r = d._stage_complete(sc(1, race_status=5))
    check("abandon: DNF marker sent", len(api.submitted), 1)
    if api.submitted:
        check("abandon: marker on the closed-out (next) stage",
              api.submitted[0]["stage_index"], 1)
        check("abandon: marker race_status preserved (5)",
              api.submitted[0]["race_status"], 5)
        check("abandon: dnf_stage_index points at the pre-persisted wrecked stage",
              api.submitted[0].get("dnf_stage_index"), 0)
    check("abandon: State (event finished)", unwrap(r["Progress"]["State"]), 2)
    check("abandon: AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 0)
    check("abandon: ChampTimeMs keeps the pre-persisted limit, no leak",
          unwrap(r["Progress"]["ChampTimeMs"]), 900000)

    # Step 3 — service-area auto-advance (rally): wreck on stage 0 of a leg
    # whose first service area sits AFTER stage 1. The skip plan must cover
    # stages 0..1 at their per-stage limits (Monte Carlo: 450/451 = 900s),
    # so the count-based progress resumes at stage 2.
    api = FakeApi(
        events={"evtT": {"events": [{
            "location": "Monte Carlo",
            "stages": [
                {"track_id": 450, "service_area": "None"},
                {"track_id": 451, "service_area": "Medium"},
                {"track_id": 454, "service_area": "None"},
            ],
        }]}},
        progress_events=[
            {"event_id": "evtT",
             "completed_stages": [stage(0, time_ms=900000)],
             "total_time_ms": 900000, "attempts_used": 0},
        ],
    )
    d = make_dispatcher(api)
    d._challenge_event_map = {800001: "evtT"}
    d._challenge_subevent_map = {800001: 0}
    r = d._stage_complete(sc(1, race_status=5))
    check("skip: DNF marker sent", len(api.submitted), 1)
    if api.submitted:
        check("skip: skip plan covers wreck + closed-out stages",
              api.submitted[0].get("dnf_stages"),
              [{"stage_index": 0, "time_ms": 900000},
               {"stage_index": 1, "time_ms": 900000}])
        check("skip: dnf_stage_index still points at the wrecked stage",
              api.submitted[0].get("dnf_stage_index"), 0)
    check("skip: State (event finished for this run)",
          unwrap(r["Progress"]["State"]), 2)
    check("skip: AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 0)

    # Step 3 — RX leg: every served RX stage has a service area, so the skip
    # plan is always exactly the wrecked stage; its limit is the per-lap base
    # (300s) times the leg's lap count (6 -> 1800s).
    api = FakeApi(
        events={"evtT": {"events": [{
            "location": "Hell",
            "rx_laps": 6,
            "stages": [
                {"track_id": 478, "service_area": "None"},
                {"track_id": 478, "service_area": "Medium"},
            ],
        }]}},
        progress_events=[
            {"event_id": "evtT",
             "completed_stages": [stage(0, time_ms=1800000)],
             "total_time_ms": 1800000, "attempts_used": 0},
        ],
    )
    d = make_dispatcher(api)
    d._challenge_event_map = {800001: "evtT"}
    d._challenge_subevent_map = {800001: 0}
    r = d._stage_complete(sc(1, race_status=5))
    check("rx: DNF marker sent", len(api.submitted), 1)
    if api.submitted:
        check("rx: skip plan = wrecked stage at per-lap x laps limit",
              api.submitted[0].get("dnf_stages"),
              [{"stage_index": 0, "time_ms": 1800000}])
    check("rx: State (event finished for this run)",
          unwrap(r["Progress"]["State"]), 2)

    print()
    if FAILS:
        print(f"{FAILS} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
