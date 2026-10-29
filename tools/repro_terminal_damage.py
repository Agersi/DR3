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

Marker payload: three close-out shapes exist (all captured instances):
mid-leg abandons advance one stage and close it out retired (rs=5, partial
time; captured 2026-10-28: wreck on stage 0 -> StageComplete(stage=1, rs=5));
a rally wreck on the leg's LAST stage closes that stage out directly
(rs=4, partial time; captured 2026-10-27/28); an RX wreck on the leg's last
stage ALSO closes it directly but with rs=5 and StageTime=0 (captured
2026-10-29: Hell semi after a genuine heat finish). An rs=5 close-out is only
treated as the advance shape when the stage before it has no stored time
strictly below its limit — the post-wreck leaderboard pre-persist stores
exactly the limit, so a DNF'd previous stage can never look genuine. The
marker carries dnf_stage_index = the resolved wrecked stage (the skip plan's
first entry) so the web side also flags the pre-persisted wrecked entry —
otherwise its time-limit value scores as a real finish.

Service areas: a stage's service_area label describes the service BEFORE that
stage (the builder's per-stage column), so the resume point is the first stage
after the wreck whose label is non-None — or the start of the following leg
when none exists, in which case the rest of this leg is skipped whole.

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

    # Club terminal-damage close-out (rs=4): a DIRECT close-out — the closed
    # out stage IS the wrecked one. A marker is sent (not a completion), no
    # phantom time.
    d, api = fresh()
    r = d._stage_complete(sc(1, race_status=4))
    check("rs=4: DNF marker sent", len(api.submitted), 1)
    if api.submitted:
        check("rs=4: marker flag", api.submitted[0].get("mark_dnf"), True)
        check("rs=4: marker time_ms is 0 (web keeps stored limit)",
              api.submitted[0]["time_ms"], 0)
        check("rs=4: marker race_status", api.submitted[0]["race_status"], 4)
        check("rs=4: direct close-out -> dnf_stage_index is the wrecked stage itself",
              api.submitted[0].get("dnf_stage_index"), 1)
        # Legacy-shape event (no service_area labels): parity fallback puts a
        # service BEFORE even stages, so the next one after wrecked stage 1 is
        # before stage 2 — only the wrecked stage is skipped.
        check("rs=4: skip plan = wrecked stage only (no limit known)",
              api.submitted[0].get("dnf_stages"),
              [{"stage_index": 1, "time_ms": 0}])
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
        check("recov: direct close-out -> dnf_stage_index is the wrecked stage itself",
              api.submitted[0].get("dnf_stage_index"), 1)
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
        # Legacy-shape leg (no labels): services before even stages -> the
        # next one after wrecked stage 0 sits before stage 2, so stages 0 and
        # 1 are skipped (limits unknown for these synthetic tracks).
        check("abandon: skip plan covers wrecked + closed-out stages",
              api.submitted[0].get("dnf_stages"),
              [{"stage_index": 0, "time_ms": 0},
               {"stage_index": 1, "time_ms": 0}])
    check("abandon: State (event finished)", unwrap(r["Progress"]["State"]), 2)
    check("abandon: AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 0)
    check("abandon: ChampTimeMs keeps the pre-persisted limit, no leak",
          unwrap(r["Progress"]["ChampTimeMs"]), 900000)

    # Step 3 — service-area auto-advance (rally): wreck on stage 0 of a leg
    # whose only service sits BEFORE stage 2 (i.e. after stage 1). The skip
    # plan must cover stages 0..1 at their per-stage limits (Monte Carlo:
    # 450/451 = 900s), so the count-based progress resumes at stage 2.
    api = FakeApi(
        events={"evtT": {"events": [{
            "location": "Monte Carlo",
            "stages": [
                {"track_id": 450, "service_area": "None"},
                {"track_id": 451, "service_area": "None"},
                {"track_id": 454, "service_area": "Medium"},
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

    # Step 3 — RX wreck on the leg's LAST stage (captured 2026-10-29, Hell
    # semi): the close-out arrives as rs=5 with StageTime=0 closing the
    # wrecked stage ITSELF, right after a genuine heat finish. The stored
    # previous time is strictly below its limit, so this is the DIRECT shape
    # — only the wrecked (closed-out) stage is skipped; the genuine heat
    # finish must NOT be re-flagged.
    api = FakeApi(
        events={"evtT": {"events": [{
            "location": "Hell",
            "rx_laps": 4,
            "stages": [
                {"track_id": 478, "service_area": "Long"},
                {"track_id": 478, "service_area": "None"},
            ],
        }]}},
        progress_events=[
            {"event_id": "evtT",
             "completed_stages": [stage(0, time_ms=184801)],
             "total_time_ms": 184801, "attempts_used": 0},
        ],
    )
    d = make_dispatcher(api)
    d._challenge_event_map = {800001: "evtT"}
    d._challenge_subevent_map = {800001: 0}
    r = d._stage_complete(sc(1, race_status=5, stage_time=0.0))
    check("rx-last-stage: DNF marker sent", len(api.submitted), 1)
    if api.submitted:
        check("rx-last-stage: genuine previous finish -> direct shape,"
              " only the wrecked stage skipped",
              api.submitted[0].get("dnf_stages"),
              [{"stage_index": 1, "time_ms": 1200000}])
        check("rx-last-stage: dnf_stage_index points at the wrecked (closed-out) stage",
              api.submitted[0].get("dnf_stage_index"), 1)
    check("rx-last-stage: State (event finished for this run)",
          unwrap(r["Progress"]["State"]), 2)
    check("rx-last-stage: AttemptsLeft", unwrap(r["Progress"]["AttemptsLeft"]), 0)

    # Step 3 — the user's reported case (Terminal Testing, Monte Carlo leg):
    # labels [Long, None] mean a service BEFORE stage 0 only (the event start)
    # and NONE between stages 1 and 2. Wreck on stage 0 (advance close-out at
    # stage 1, rs=5): no later stage carries a service, so the whole rest of
    # the leg is skipped — resume belongs to the following leg, not stage 2.
    api = FakeApi(
        events={"evtT": {"events": [{
            "location": "Monte Carlo",
            "stages": [
                {"track_id": 450, "service_area": "Long"},
                {"track_id": 453, "service_area": "None"},
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
    check("terminal-testing: DNF marker sent", len(api.submitted), 1)
    if api.submitted:
        check("terminal-testing: no service after the wreck -> whole leg skipped",
              api.submitted[0].get("dnf_stages"),
              [{"stage_index": 0, "time_ms": 900000},
               {"stage_index": 1, "time_ms": 900000}])
        check("terminal-testing: dnf_stage_index points at the wrecked stage",
              api.submitted[0].get("dnf_stage_index"), 0)
    check("terminal-testing: State (event finished for this run)",
          unwrap(r["Progress"]["State"]), 2)

    # Wreck on the leg's LAST stage (captured shape: direct close-out of the
    # wrecked stage, rs=4, partial time). Only that stage is skipped — the
    # previous clean finish must NOT be re-flagged.
    api = FakeApi(
        events={"evtT": {"events": [{
            "location": "Monte Carlo",
            "stages": [
                {"track_id": 450, "service_area": "Long"},
                {"track_id": 453, "service_area": "None"},
            ],
        }]}},
        progress_events=[
            {"event_id": "evtT",
             "completed_stages": [stage(0, time_ms=285116)],
             "total_time_ms": 285116, "attempts_used": 0},
        ],
    )
    d = make_dispatcher(api)
    d._challenge_event_map = {800001: "evtT"}
    d._challenge_subevent_map = {800001: 0}
    r = d._stage_complete(sc(1, race_status=4, stage_time=31.5))
    check("last-stage wreck: DNF marker sent", len(api.submitted), 1)
    if api.submitted:
        check("last-stage wreck: direct close-out -> dnf_stage_index is the wrecked stage itself",
              api.submitted[0].get("dnf_stage_index"), 1)
        check("last-stage wreck: skip plan = wrecked stage only",
              api.submitted[0].get("dnf_stages"),
              [{"stage_index": 1, "time_ms": 900000}])
    check("last-stage wreck: State (event finished for this run)",
          unwrap(r["Progress"]["State"]), 2)

    # Carry-over into the NEXT leg after a DNF (crash 2026-10-29, +0x106590):
    # the last completed entry is the sparse advance close-out (web falls back
    # its vehicle to the Result column but has no livery). The carry-over must
    # walk back to the nearest stage with a real livery — never serve 0.
    def sparse(idx, vehicle_id):
        return {"stage_index": idx, "time_ms": 900000,
                "vehicle_id": vehicle_id, "livery_id": 0,
                "race_status": 5}

    api = FakeApi(
        events={"evtT": {"stages": [stage(0), stage(1)]}},
        progress_events=[
            {"event_id": "evtT",
             "completed_stages": [
                 {**stage(0, time_ms=900000), "vehicle_id": 564,
                  "livery_id": 3368},
                 sparse(1, 564),
             ],
             "total_time_ms": 1800000, "attempts_used": 0},
        ],
    )
    d = make_dispatcher(api)
    p = d._multi_event_progress(800002, [2, 2, 2], 1,
                                api.progress["events"][0], carry_over=True)
    check("carry-dnf: Progress entry served", p is not None, True)
    if p:
        check("carry-dnf: vehicle from the real stage, livery walked back"
              " past the sparse close-out",
              (unwrap(p["VehicleId"]), unwrap(p["LiveryId"])),
              (564, 3368))

    # No captured livery anywhere -> the default-livery table must cover the
    # vehicle (564 was missing from it before 2026-10-29).
    api = FakeApi(
        events={"evtT": {"stages": [stage(0), stage(1)]}},
        progress_events=[
            {"event_id": "evtT",
             "completed_stages": [
                 {**stage(0, time_ms=900000), "vehicle_id": 564,
                  "livery_id": 0},
                 sparse(1, 564),
             ],
             "total_time_ms": 1800000, "attempts_used": 0},
        ],
    )
    d = make_dispatcher(api)
    p = d._multi_event_progress(800002, [2, 2, 2], 1,
                                api.progress["events"][0], carry_over=True)
    check("carry-table: livery from the default table",
          (unwrap(p["VehicleId"]), unwrap(p["LiveryId"])), (564, 3673))

    # Service-area retirement (2026-10-29 crash, +0x100840): the player
    # retires BEFORE any stage-begin capture, so the web side has no vehicle
    # data at all (pre-persist row is time-only). The close-out payload itself
    # carries the car the player was driving — the marker must pass it through
    # so the web can merge it into the flagged entries (leaderboard car/class/
    # flag + next-leg carry-over); without it the next leg is served with
    # VehicleId=0/LiveryId=0 and the game crashes on Start.
    api = FakeApi(
        events={"evtT": {"events": [{
            "location": "Monte Carlo",
            "stages": [
                {"track_id": 450, "service_area": "Long"},
                {"track_id": 453, "service_area": "None"},
            ],
        }]}},
        progress_events=[
            # Only the post-wreck pre-persist exists (time limit, no vehicle).
            {"event_id": "evtT",
             "completed_stages": [{"stage_index": 0, "time_ms": 900000}],
             "total_time_ms": 900000, "attempts_used": 0},
        ],
    )
    d = make_dispatcher(api)
    d._challenge_event_map = {800001: "evtT"}
    d._challenge_subevent_map = {800001: 0}
    r = d._stage_complete(sc(1, race_status=5, stage_time=0.0))
    check("service-area retire: DNF marker sent", len(api.submitted), 1)
    if api.submitted:
        s = api.submitted[0]
        check("service-area retire: marker carries the payload's vehicle",
              s.get("vehicle_id"), 469)
        check("service-area retire: marker carries the payload's livery",
              s.get("livery_id"), 3233)
        check("service-area retire: marker carries the payload's nationality",
              s.get("nationality_id"), 31)
        check("service-area retire: advance shape -> wrecked stage 0, whole leg skipped",
              (s.get("dnf_stage_index"), s.get("dnf_stages")),
              (0, [{"stage_index": 0, "time_ms": 900000},
                   {"stage_index": 1, "time_ms": 900000}]))
    check("service-area retire: State=2 (leg finished for this run)",
          unwrap(r["Progress"]["State"]), 2)

    print()
    if FAILS:
        print(f"{FAILS} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
