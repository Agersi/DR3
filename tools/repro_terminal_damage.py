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
value must not leak into the echoed ChampTimeMs. The Progress response is still
built for every shape (state machine unchanged — step 3 territory).

Run: python tools\repro_terminal_damage.py   (from D:\CODE\DR2)
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
    def __init__(self, events=None, progress_events=None):
        self.events = events or {}
        self.progress = {"events": progress_events or []}
        self.submitted = []

    def get_my_progress(self):
        return self.progress

    def get_event(self, event_id):
        return self.events.get(event_id)

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
    check("rs=4: State (mid-leg)", unwrap(r["Progress"]["State"]), 0)
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
    check("recov: State (mid-leg)", unwrap(r["Progress"]["State"]), 0)
    check("recov: ChampTimeMs does not absorb the limit value",
          unwrap(r["Progress"]["ChampTimeMs"]), 1000)

    print()
    if FAILS:
        print(f"{FAILS} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
