r"""Verify the local RaceNetChallenges.GetChallengeRecap handler (backlog #19).

The event-detail "Event Progress" tab is built from GetChallengeRecap. Club
challenge ids used to be proxied upstream, which returned an empty Recap for
them (verified in captures: every 2xxxxx recap came back with Recap=[] while
official id 947604 returned 27 populated entries). This script checks the new
local handler at unit level:

  - routing: GetChallengeRecap is challenge-routed (club ids local, upstream
    official ids still proxied);
  - fully completed leg -> every entry State=2 with per-stage times and a
    running CumulTimeMs;
  - partially completed leg -> finished stages State=2, the rest State=0 with
    the running cumulative carried forward;
  - unstarted leg -> all State=0 / zero times;
  - multi-event championship (sub_index > 0) -> only that leg's stages are
    covered and CumulTimeMs restarts at 0 for the leg (per-leg model);
  - unknown challenge id -> empty Recap (safe default);
  - wire roundtrip: encode_stream/decode_stream preserves values and the
    ui32 typing of the time fields.

Run: python tools\repro_challenge_recap.py   (from D:\CODE\DR2)
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore  # noqa: E402
from dr2server.dispatcher import RpcDispatcher, _stable_int_id  # noqa: E402
from dr2server.egonet import UInt32, decode_stream, encode_stream  # noqa: E402

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
    """Just enough of UpstreamClient for the recap code paths."""

    def __init__(self, events=None, progress_events=None):
        self.events = events or {}
        self.progress = {"events": progress_events or []}

    def get_event(self, event_id):
        return self.events.get(event_id)

    def get_my_progress(self):
        return self.progress

    # Refresh-path stubs (only hit for unknown challenge ids).
    def get_clubs(self):
        return {"clubs": [], "events": []}

    def get_challenges(self):
        return []


def make_dispatcher(api):
    store = AccountStore(Path(r"D:\CODE\DR2\build\recap_test_accounts") / "u")
    return RpcDispatcher(store, api_client=api)


def recap(d, chal):
    resp = d._challenge_recap({"ChallengeId": chal})
    assert resp.get("ok"), f"recap failed: {resp}"
    return resp["Recap"]


def main():
    import dr2server.httpd as httpd

    # --------------------------------------------------------------- routing
    check("routing: GetChallengeRecap in CHALLENGE_ROUTED_METHODS",
          "RaceNetChallenges.GetChallengeRecap" in httpd.CHALLENGE_ROUTED_METHODS, True)

    api = FakeApi()
    d = make_dispatcher(api)
    club_chal = _stable_int_id("evtR", base=200000)
    upstream_chal = 947604  # from captures; outside the 200k-400k local range
    d._challenge_event_map[club_chal] = "evtR"
    check("routing: club id is_local_challenge", d.is_local_challenge({"ChallengeId": club_chal}), True)
    check("routing: upstream official id still proxies", d.is_local_challenge({"ChallengeId": upstream_chal}), False)

    # ------------------------------------------------- single-event rally leg
    api.events["evtR"] = {"stages": [1, 2, 3]}  # legacy single-event shape

    ep_full = {"event_id": "evtR", "completed_stages": [
        {"stage_index": 0, "time_ms": 10000},
        {"stage_index": 1, "time_ms": 20000},
        {"stage_index": 2, "time_ms": 30000},
    ]}
    api.progress["events"] = [ep_full]

    r = recap(d, club_chal)
    check("completed: entry count", len(r), 3)
    if len(r) == 3:
        check("completed: all State=2", [e["State"] for e in r], [2, 2, 2])
        check("completed: StageTimeMs per stage",
              [unwrap(e["StageTimeMs"]) for e in r], [10000, 20000, 30000])
        check("completed: CumulTimeMs running total",
              [unwrap(e["CumulTimeMs"]) for e in r], [10000, 30000, 60000])

    ep_part = {"event_id": "evtR", "completed_stages": [
        {"stage_index": 0, "time_ms": 10000},
    ]}
    api.progress["events"] = [ep_part]
    r = recap(d, club_chal)
    check("partial: entry count", len(r), 3)
    if len(r) == 3:
        check("partial: states [2,0,0]", [e["State"] for e in r], [2, 0, 0])
        check("partial: StageTimeMs", [unwrap(e["StageTimeMs"]) for e in r], [10000, 0, 0])
        check("partial: CumulTimeMs carried forward",
              [unwrap(e["CumulTimeMs"]) for e in r], [10000, 10000, 10000])

    api.progress["events"] = []
    r = recap(d, club_chal)
    check("unstarted: entry count", len(r), 3)
    if len(r) == 3:
        check("unstarted: all State=0", [e["State"] for e in r], [0, 0, 0])
    if len(r) == 3:
        check("unstarted: zero times",
              [unwrap(e["StageTimeMs"]) for e in r] + [unwrap(e["CumulTimeMs"]) for e in r],
              [0, 0, 0, 0, 0, 0])

    # ------------------------------------------- multi-event championship leg
    # Championship: leg0 = 2 stages, leg1 = 3 stages. Leg 0 fully done,
    # leg 1 partially done (its first stage). Flat ordinals are
    # championship-wide: gidx 0,1 -> leg0; gidx 2,3,4 -> leg1.
    api.events["evtM"] = {"events": [
        {"stages": [1, 1]},
        {"stages": [1, 1, 1]},
    ]}
    base_m = _stable_int_id("evtM", base=200000)
    chal_m1 = base_m + 1  # leg 1 sub-event id (base + sub_index, per _serve_multi_event_club)
    api.progress["events"] = [
        {"event_id": "evtM", "completed_stages": [
            {"stage_index": 0, "time_ms": 5000},   # leg0 s0
            {"stage_index": 1, "time_ms": 6000},   # leg0 s1
            {"stage_index": 2, "time_ms": 7000},   # leg1 s0 (gidx 2)
        ]}
    ]
    d = make_dispatcher(api)
    d._challenge_event_map[chal_m1] = "evtM"
    d._challenge_subevent_map[chal_m1] = 1

    r = recap(d, chal_m1)
    check("multi leg1: entry count (leg stages only)", len(r), 3)
    if len(r) == 3:
        check("multi leg1: EventIndex all 0", [e["EventIndex"] for e in r], [0, 0, 0])
        check("multi leg1: StageIndex 0..2", [e["StageIndex"] for e in r], [0, 1, 2])
        check("multi leg1: states [2,0,0]", [e["State"] for e in r], [2, 0, 0])
        check("multi leg1: per-leg cumulative restarts at 0",
              [unwrap(e["CumulTimeMs"]) for e in r], [7000, 7000, 7000])

    chal_m0 = base_m + 0
    d._challenge_event_map[chal_m0] = "evtM"
    d._challenge_subevent_map[chal_m0] = 0
    r = recap(d, chal_m0)
    check("multi leg0: entry count", len(r), 2)
    if len(r) == 2:
        check("multi leg0: states [2,2]", [e["State"] for e in r], [2, 2])
        check("multi leg0: per-leg cumulative",
              [unwrap(e["CumulTimeMs"]) for e in r], [5000, 11000])

    # ----------------------------------------------------------- unknown ids
    check("unknown upstream id: empty Recap", recap(d, 947604), [])
    check("unknown club id: empty Recap", recap(d, _stable_int_id("nope", base=200000)), [])
    check("zero id: empty Recap", recap(d, 0), [])

    # ------------------------------------------------- wire encoding roundtrip
    api.progress["events"] = [ep_full]
    dw = make_dispatcher(api)
    dw._challenge_event_map[club_chal] = "evtR"
    resp = dw._challenge_recap({"ChallengeId": club_chal})
    raw = encode_stream(resp)
    decoded = decode_stream(raw)
    entries = decoded.get("Recap", [])
    check("wire: decodes to 3 entries", len(entries), 3)
    if len(entries) == 3:
        ok = all(isinstance(e["StageTimeMs"], UInt32) and isinstance(e["CumulTimeMs"], UInt32)
                 for e in entries)
        check("wire: time fields encoded as ui32", ok, True)
        check("wire: values survive roundtrip",
              [(e["ChallengeId"], e["EventIndex"], e["StageIndex"], e["State"])
               for e in entries],
              [(club_chal, 0, i, 2) for i in range(3)])

    print()
    if FAILS:
        print(f"{FAILS} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
