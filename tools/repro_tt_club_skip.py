#!/usr/bin/env python3
"""Verify the PostTime recording rules in RpcDispatcher.

The game fires TimeTrial.PostTime after EVERY completed stage — club, official
and time trials (identical payload shape, always carrying the run's ghost;
captures 2026-11-04/05: all 24 PostTimes carry GhostData + TrackModelId +
ConditionsId). Every one of them is now recorded locally as the driver's
personal best for car + track + conditions (best_times); the request is still
proxied to EGO untouched.

Rules under test:
- Splits are claimed from the UDP run HERE (official/TT StageCompletes carry no
  telemetry) and stashed per vehicle for the StageComplete that follows
  (httpd._record_proxy_stats hands them to the stage_runs merge feed).
- While a club stage on a track is open, its PostTime still records best +
  ghost but must NOT claim the UDP run — the club StageComplete path owns it
  (#49) and feeds its splits into the same best row seconds later.
- Official StageBegin arms the telemetry run clock (the dispatcher never sees
  proxied begins). Stashes expire after _POSTTIME_SPLITS_TTL_S.

Run: python tools/repro_tt_club_skip.py   (from D:\\CODE\\DR2)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore
from dr2server.api_client import _location_id_for
from dr2server.dispatcher import RpcDispatcher, VEHICLES

FAILS = 0

EVENT_ID = "evtT"
CHAL = 800001

# Leg: stage 0 on track 636, stage 1 on track 630 (Han's-FWDs-shaped).
EVENT = {"events": [{"stages": [{"track_id": 636}, {"track_id": 630}]}]}


def check(label, got, want):
    global FAILS
    ok = got == want
    print(f"[{'OK' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS += 1


class FakeApi:
    def __init__(self):
        self.events = {EVENT_ID: EVENT}
        self.progress = {"events": []}
        self.tt_submits = []

    def get_my_progress(self):
        return self.progress

    def get_event(self, event_id):
        return self.events.get(event_id)

    def resolve_location_id(self, location_name):
        return _location_id_for(location_name)

    def submit_stage_begin(self, **kw):
        pass

    def submit_stage(self, **kw):
        return {"ok": True}

    def submit_time_trial(self, **kw):
        self.tt_submits.append(kw)
        return True

    def record_stats(self, **kw):
        pass


class FakeRun:
    def __init__(self, segs):
        self._segs = segs

    def segment_times(self, n):
        return list(self._segs[:n]) if n else []

    def has_wrap(self):
        return False

    def max_distance(self):
        return 0.0


class FakeTelemetry:
    """Counts take_run_since claims; returns the fixed run (or None)."""

    def __init__(self, run=None):
        self.run = run
        self.claims = 0

    def take_run_since(self, wall):
        self.claims += 1
        return self.run


def make_dispatcher(telemetry=None, last_tt=(107, 636, 5, 2)):
    store = AccountStore(Path(r"D:\CODE\DR2\build\tt_club_skip_accounts") / "u")
    d = RpcDispatcher(store, api_client=FakeApi())
    d._challenge_event_map[CHAL] = EVENT_ID
    d._challenge_subevent_map[CHAL] = 0
    d._last_tt_request = last_tt
    d.telemetry = telemetry
    return d


def tt_params(track=636, time_s=95.0, vehicle=513):
    return {
        "TrackModelId": track,
        "VehicleClassId": 107,
        "ConditionsId": 5,
        "Category": 2,
        "StageTime": time_s,
        "VehicleId": vehicle,
        "LiveryId": 4819,
        "GhostData": b"\x01\x02ghost",
    }


def sb_params(stage=0):
    return {
        "VehicleId": 513, "VehicleInstId": -4, "LiveryId": 4819,
        "StageIndex": stage, "EventIndex": 0, "ChallengeId": CHAL,
        "NationalityId": 31, "TyresRemaining": 1, "TyreCompound": 6,
    }


def sc_params(stage=0, race_status=0):
    return {
        "VehicleId": 513, "VehicleInstId": -4, "LiveryId": 4819,
        "StageIndex": stage, "EventIndex": 0, "ChallengeId": CHAL,
        "NationalityId": 31, "StageTime": 95.0,
        "MetersDriven": 1234, "DistanceDriven": 1234,
        "RaceStatus": race_status, "RecovToService": False,
    }


def off_sb(stage=0, vehicle=513):
    return {"ChallengeId": 947767, "EventIndex": 0, "StageIndex": stage,
            "VehicleId": vehicle, "LiveryId": 4819}


def main():
    # (a) real TT with telemetry -> recorded WITH splits, stashed for the
    # StageComplete that follows.
    d = make_dispatcher(FakeTelemetry(FakeRun([25.0] * 4)))
    out = d._post_time(tt_params(track=636))
    check("a: accepted", out.get("Accepted"), True)
    check("a: best submitted", len(d.api_client.tt_submits), 1)
    splits = d.api_client.tt_submits[0].get("splits")
    check("a: splits claimed (4 segments)", None if splits is None else len(splits), 4)
    check("a: splits scaled to stage time",
          None if splits is None else round(sum(splits), 3), 95.0)
    check("a: ghost carried", d.api_client.tt_submits[0].get("ghost_data_b64") != "", True)
    stashed = d._pending_posttime_splits.get(513)
    check("a: splits stashed for vehicle", None if stashed is None else len(stashed[1] or []), 4)

    # (b) club stage on 636 open -> PostTime on 636 STILL records best + ghost,
    # but must NOT claim the UDP run (the club StageComplete owns it).
    d = make_dispatcher(FakeTelemetry())
    d._stage_begin(sb_params(stage=0))
    check("b: stage 0 marked open on 636",
          d._open_club_stages.get((CHAL, 0, 0)), 636)
    claims_before = d.telemetry.claims
    out = d._post_time(tt_params(track=636))
    check("b: accepted (EGO still happy)", out.get("Accepted"), True)
    check("b: best + ghost recorded", len(d.api_client.tt_submits), 1)
    check("b: no splits claimed for club run",
          d.api_client.tt_submits[0].get("splits"), None)
    check("b: UDP run NOT claimed", d.telemetry.claims, claims_before)
    check("b: nothing stashed for club run", dict(d._pending_posttime_splits), {})

    # (c) guard is track-scoped: TT on another track still claims + records.
    d.telemetry.run = FakeRun([20.0] * 4)
    out = d._post_time(tt_params(track=450))
    check("c: accepted", out.get("Accepted"), True)
    check("c: other-track TT recorded", len(d.api_client.tt_submits), 2)
    check("c: UDP run claimed", d.telemetry.claims, claims_before + 1)

    # (d) clean StageComplete clears the mark -> 636 claims again.
    d._stage_complete(sc_params(stage=0, race_status=0))
    check("d: open map cleared after complete", dict(d._open_club_stages), {})
    claims_before = d.telemetry.claims
    d._post_time(tt_params(track=636))
    check("d: 636 claims again after close-out", d.telemetry.claims, claims_before + 1)

    # (e) DNF close-out also clears the mark.
    d._stage_begin(sb_params(stage=1))
    check("e: stage 1 marked open on 630",
          d._open_club_stages.get((CHAL, 0, 1)), 630)
    d._stage_complete(sc_params(stage=1, race_status=4))
    check("e: open map cleared after DNF close-out", dict(d._open_club_stages), {})

    # (f) negative control: track 0 never matches.
    check("f: track 0 never open", d._club_stage_open_on(0), False)

    # --- Official stages: PostTimes are recorded locally too (dual mode — EGO
    # keeps its global board); the proxied StageBegin only arms the run clock.

    # (g) official StageBegin arms the telemetry wall; the PostTime that
    # follows is recorded + claims the run + stashes splits.
    d = make_dispatcher(FakeTelemetry(FakeRun([30.0] * 4)))
    d._telemetry_arm_wall = 0.0
    d.note_official_stage_begin(off_sb(stage=0))
    check("g: official begin arms the run clock", d._telemetry_arm_wall > 0, True)
    claims_before = d.telemetry.claims
    out = d._post_time(tt_params(track=636))
    check("g: accepted (EGO still happy)", out.get("Accepted"), True)
    check("g: official PostTime recorded locally", len(d.api_client.tt_submits), 1)
    check("g: UDP run claimed", d.telemetry.claims, claims_before + 1)
    stashed = d._pending_posttime_splits.get(513)
    check("g: splits stashed for the following StageComplete",
          None if stashed is None else len(stashed[1] or []), 4)

    # (h) take_posttime_splits pops the stash; a second take finds nothing.
    first = d.take_posttime_splits(513)
    check("h: stash popped with splits", None if first is None else len(first), 4)
    check("h: second take is empty", d.take_posttime_splits(513), None)

    # (i) stale stash entry (quit between PostTime and StageComplete) expires.
    d._stash_posttime_splits(513, [1.0, 2.0])
    d._pending_posttime_splits[513] = (time.time() - 121.0, [1.0, 2.0])
    check("i: stale entry does not hand over", d.take_posttime_splits(513), None)

    # (j) vclass comes from the per-car catalog when no GetLeaderboardId cache
    # exists (fresh session, official stage).
    d = make_dispatcher(last_tt=None)
    d._post_time(tt_params(track=636))
    check("j: vclass from vehicle catalog",
          d.api_client.tt_submits[0].get("vehicle_class_id"),
          VEHICLES[513]["class"])

    print(f"\n{'ALL PASS' if FAILS == 0 else str(FAILS) + ' FAILURE(S)'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
