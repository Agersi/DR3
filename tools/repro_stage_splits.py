r"""Verify GetStageSplits serving (fastest-time-in-event split synthesis).

Upstream wire shape captured 2026-10-31 from the last official EGO net club
(tools/probe_upstream_splits.py, build/upstream_split_captures/):
    {"SplitsExist": true, "Splits": [{"Time": f32}, ...], "TotalTime": f32}
Per-segment times sum to TotalTime; the split count follows the stage's
max-time limit (900000ms -> 4, 1800000ms -> 8). The game sends no split data
itself, so we synthesize an even profile from the event's fastest clean
stage time. Any missing piece (unknown track, no clean finish, unknown
challenge) keeps the legacy empty stub — byte-identical to pre-fix behavior.

Run: python tools\repro_stage_splits.py   (from D:\CODE\DR2)
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore
from dr2server.api_client import _location_id_for
from dr2server.dispatcher import RpcDispatcher
from dr2server.egonet import UInt32, decode_stream, encode_stream

FAILS = 0


def check(label, got, want):
    global FAILS
    ok = got == want
    print(f"[{'OK' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS += 1


class FakeApi:
    def __init__(self, events=None, leaderboard=None):
        self.events = events or {}
        self.leaderboard = leaderboard or []

    def get_event(self, event_id):
        return self.events.get(event_id)

    def resolve_location_id(self, location_name):
        return _location_id_for(location_name)

    def get_leaderboard(self, event_id):
        return self.leaderboard


def make_dispatcher(api):
    store = AccountStore(Path(r"D:\CODE\DR2\build\stage_splits_test_accounts") / "u")
    d = RpcDispatcher(store, api_client=api)
    d._challenge_event_map = {900001: "evtS"}
    d._challenge_subevent_map = {900001: 0}
    return d


def entry(time_ms, rs=0):
    return {"time_ms": time_ms, "race_status": rs}


LEG_900K = {"stages": [
    {"track_id": 450},   # Monte Carlo Gordolon — 900000ms -> 4 splits
]}
LEG_1800K = {"stages": [
    {"track_id": 435},   # Monte Carlo Pra d'Alart — 1800000ms -> 8 splits
]}
RX_LEG = {"location": "Hell", "stages": [{"track_id": 478}], "rx_laps": 6}


def stub_only(r):
    return {k: v for k, v in r.items() if k != "ok"}


def main():
    # (a) fastest clean time on a 15-min stage -> 4 even segments summing to it.
    api = FakeApi(events={"evtS": LEG_900K}, leaderboard=[
        {"username": "a", "stages": [entry(300000)]},
        {"username": "b", "stages": [entry(240000)]},
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 900001, "EventIndex": 0, "StageIndex": 0})
    check("a: SplitsExist", r.get("SplitsExist"), True)
    check("a: split count", len(r.get("Splits", [])), 4)
    check("a: TotalTime is the fastest time", r.get("TotalTime"), 240.0)
    check("a: segments sum to total",
          round(sum(s["Time"] for s in r["Splits"]), 6), 240.0)

    # (b) 30-min stage -> 8 segments.
    api = FakeApi(events={"evtS": LEG_1800K}, leaderboard=[
        {"username": "a", "stages": [entry(600000)]},
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 900001, "EventIndex": 0, "StageIndex": 0})
    check("b: split count", len(r.get("Splits", [])), 8)
    check("b: TotalTime", r.get("TotalTime"), 600.0)

    # (c) nobody finished the stage -> legacy stub.
    api = FakeApi(events={"evtS": LEG_900K}, leaderboard=[])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 900001, "EventIndex": 0, "StageIndex": 0})
    check("c: no entries -> legacy stub", stub_only(r), {"Splits": [], "Entries": []})

    # (d) DNF-only entries -> legacy stub.
    api = FakeApi(events={"evtS": LEG_900K}, leaderboard=[
        {"username": "a", "stages": [entry(900000, rs=5)]},
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 900001, "EventIndex": 0, "StageIndex": 0})
    check("d: DNF-only -> stub", stub_only(r), {"Splits": [], "Entries": []})

    # (e) pre-persist signature (stored == limit, rs=0) is not a real finish.
    api = FakeApi(events={"evtS": LEG_900K}, leaderboard=[
        {"username": "a", "stages": [entry(900000)]},
        {"username": "b", "stages": [entry(850000)]},
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 900001, "EventIndex": 0, "StageIndex": 0})
    check("e: limit-value row ignored", r.get("TotalTime"), 850.0)

    # (f) unknown track (no limit row) -> legacy stub.
    api = FakeApi(events={"evtS": {"stages": [{"track_id": 1}]}}, leaderboard=[
        {"username": "a", "stages": [entry(300000)]},
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 900001, "EventIndex": 0, "StageIndex": 0})
    check("f: unknown track -> stub", stub_only(r), {"Splits": [], "Entries": []})

    # (g) RX leg with rx_laps=6 -> one split per lap = 6 segments (the rally
    # max-time -> count map doesn't apply to RX; the old code coincidentally
    # gave 8 via the 1800000 limit row, which was wrong).
    api = FakeApi(events={"evtS": RX_LEG}, leaderboard=[
        {"username": "a", "stages": [entry(180000)]},
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 900001, "EventIndex": 0, "StageIndex": 0})
    check("g: RX split count (one per lap)", len(r.get("Splits", [])), 6)
    check("g: RX TotalTime", r.get("TotalTime"), 180.0)

    # (h) unknown challenge id -> legacy stub (no wrong-event data).
    api = FakeApi(events={"evtS": LEG_900K}, leaderboard=[
        {"username": "a", "stages": [entry(300000)]},
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 424242, "EventIndex": 0, "StageIndex": 0})
    check("h: unknown challenge -> stub", stub_only(r), {"Splits": [], "Entries": []})

    # (i) wire round-trip: encodes to the exact upstream vdic shape.
    api = FakeApi(events={"evtS": LEG_900K}, leaderboard=[
        {"username": "a", "stages": [entry(240000)]},
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 900001, "EventIndex": 0, "StageIndex": 0})
    wire = encode_stream({k: v for k, v in r.items() if k != "ok"})
    dec = decode_stream(wire)
    check("i: decoded keys", sorted(dec.keys()), ["Splits", "SplitsExist", "TotalTime"])
    check("i: SplitsExist is bool", isinstance(dec["SplitsExist"], bool), True)
    check("i: Time values are floats",
          all(isinstance(s["Time"], float) for s in dec["Splits"]), True)
    check("i: TotalTime is float", isinstance(dec["TotalTime"], float), True)

    # (j) production shape: the decoder preserves scalar types via wrappers, so
    #     a real request arrives with UInt32 values, not plain ints.
    api = FakeApi(events={"evtS": LEG_900K}, leaderboard=[
        {"username": "a", "stages": [entry(240000)]},
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({
        "ChallengeId": UInt32(900001),
        "EventIndex": UInt32(0),
        "StageIndex": UInt32(0),
    })
    check("j: wrapper params parsed", r.get("SplitsExist"), True)
    check("j: wrapper TotalTime", r.get("TotalTime"), 240.0)

    # (k) a fastest entry that carries stored telemetry splits is served verbatim
    #     (the real per-segment profile, not an even synthesis).
    api = FakeApi(events={"evtS": LEG_900K}, leaderboard=[
        {"username": "a", "stages": [{"time_ms": 300000, "race_status": 0}]},
        {"username": "b", "stages": [
            {"time_ms": 240000, "race_status": 0,
             "splits": [61.5, 58.2, 57.3, 63.0]}]},   # sums to 240.0
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 900001, "EventIndex": 0, "StageIndex": 0})
    check("k: real splits served verbatim",
          [s["Time"] for s in r["Splits"]], [61.5, 58.2, 57.3, 63.0])
    check("k: TotalTime is the fastest time", r.get("TotalTime"), 240.0)

    # (l) stored splits of the wrong length for this stage fall back to even.
    api = FakeApi(events={"evtS": LEG_900K}, leaderboard=[
        {"username": "a", "stages": [
            {"time_ms": 240000, "race_status": 0, "splits": [80.0, 80.0]}]},
    ])
    d = make_dispatcher(api)
    r = d._stage_splits({"ChallengeId": 900001, "EventIndex": 0, "StageIndex": 0})
    check("l: wrong-length splits -> even count", len(r.get("Splits", [])), 4)
    check("l: even segments sum to total",
          round(sum(s["Time"] for s in r["Splits"]), 6), 240.0)

    print(f"\n{'ALL PASS' if FAILS == 0 else str(FAILS) + ' FAILURE(S)'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
