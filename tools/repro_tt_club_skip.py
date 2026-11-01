#!/usr/bin/env python3
"""Verify the #49 club-stage PostTime skip in RpcDispatcher.

The game fires TimeTrial.PostTime after every completed CLUB stage with the
identical payload shape of a real time trial (captures 2026-10-30: club stages
are StageBegin -> PostTime -> StageComplete, no GetLeaderboardId; real TTs are
always GetLeaderboardId -> ... -> PostTime). Recording those locally filed
club times into time_trials and created spurious 'timetrial' stage_stats rows
seconds before the club StageComplete arrived.

Fix under test: a club stage begun but not yet completed marks its track as
in-progress (StageBegin -> _open_club_stages, cleared on any StageComplete);
a PostTime on an open track is answered Accepted without the local web calls.
The guard is track-scoped, so a TT on another track still records.

Run: python tools/repro_tt_club_skip.py   (from D:\\CODE\\DR2)
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore
from dr2server.api_client import _location_id_for
from dr2server.dispatcher import RpcDispatcher

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
        self.stats_records = []

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

    def record_stats(self, **kw):
        self.stats_records.append(kw)


def make_dispatcher():
    store = AccountStore(Path(r"D:\CODE\DR2\build\tt_club_skip_accounts") / "u")
    d = RpcDispatcher(store, api_client=FakeApi())
    d._challenge_event_map[CHAL] = EVENT_ID
    d._challenge_subevent_map[CHAL] = 0
    d._last_tt_request = (107, 636, 5, 2)
    return d


def tt_params(track=636, time_s=95.0):
    return {
        "TrackModelId": track,
        "VehicleClassId": 107,
        "ConditionsId": 5,
        "Category": 2,
        "StageTime": time_s,
        "VehicleId": 513,
        "LiveryId": 4819,
        "GhostData": b"",
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


def main():
    # (a) real TT with no club stage open -> recorded.
    d = make_dispatcher()
    out = d._post_time(tt_params(track=636))
    check("a: accepted", out.get("Accepted"), True)
    check("a: time_trial submitted", len(d.api_client.tt_submits), 1)
    check("a: stats recorded as timetrial",
          [r.get("source_type") for r in d.api_client.stats_records], ["timetrial"])

    # (b) club stage on 636 open -> PostTime on 636 is skipped.
    d = make_dispatcher()
    d._stage_begin(sb_params(stage=0))
    check("b: stage 0 marked open on 636",
          d._open_club_stages.get((CHAL, 0, 0)), 636)
    before = len(d.api_client.tt_submits)
    out = d._post_time(tt_params(track=636))
    check("b: accepted (EGO still happy)", out.get("Accepted"), True)
    check("b: NOT recorded while club stage open",
          len(d.api_client.tt_submits), before)
    check("b: no stats row created", len(d.api_client.stats_records), 0)

    # (c) guard is track-scoped: TT on another track still records.
    out = d._post_time(tt_params(track=450))
    check("c: accepted", out.get("Accepted"), True)
    check("c: other-track TT recorded", len(d.api_client.tt_submits), 1)

    # (d) clean StageComplete clears the mark -> 636 records again.
    d._stage_complete(sc_params(stage=0, race_status=0))
    check("d: open map cleared after complete", dict(d._open_club_stages), {})
    before = len(d.api_client.tt_submits)
    d._post_time(tt_params(track=636))
    check("d: 636 records again after close-out",
          len(d.api_client.tt_submits), before + 1)

    # (e) DNF close-out also clears the mark.
    d._stage_begin(sb_params(stage=1))
    check("e: stage 1 marked open on 630",
          d._open_club_stages.get((CHAL, 0, 1)), 630)
    d._stage_complete(sc_params(stage=1, race_status=4))
    check("e: open map cleared after DNF close-out", dict(d._open_club_stages), {})

    # (f) negative control: track 0 never matches.
    check("f: track 0 never open", d._club_stage_open_on(0), False)

    print(f"\n{'ALL PASS' if FAILS == 0 else str(FAILS) + ' FAILURE(S)'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
