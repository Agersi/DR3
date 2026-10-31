#!/usr/bin/env python3
"""Verify the dispatcher's telemetry -> splits wiring end to end (no socket).

Replays the German TT UDP capture through TelemetryListener to obtain a real
completed StageRun, then feeds it to RpcDispatcher._compute_stage_splits via a
fake telemetry object and checks the segments are scaled to sum exactly to the
submitted official stage time. Also covers the no-telemetry / no-run fallbacks.

Run: python tools/repro_telemetry_wiring.py   (from D:\\CODE\\DR2)
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore
from dr2server.api_client import _location_id_for
from dr2server.dispatcher import RpcDispatcher
from dr2server.telemetry import TelemetryListener

FAILS = 0


def check(label, got, want):
    global FAILS
    ok = got == want
    print(f"[{'OK' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS += 1


CAPTURE = Path(r"D:\CODE\DR2\build\dr2_udp_captures\splits-probe.jsonl")

LEG_900K = {"stages": [{"track_id": 450}]}   # Monte Carlo Gordolon -> 900000ms -> 4 splits


class FakeApi:
    def __init__(self):
        self.events = {"evtS": LEG_900K}

    def get_event(self, event_id):
        return self.events.get(event_id)

    def resolve_location_id(self, location_name):
        return _location_id_for(location_name)


class FakeTelemetry:
    """Returns the given run once, then nothing (mirrors a claimed run)."""

    def __init__(self, run):
        self._run = run

    def take_run_since(self, since_wall, margin=1.0):
        r = self._run
        self._run = None
        return r


def load_run():
    import json
    import struct
    listener = TelemetryListener(port=0)
    with open(CAPTURE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("len") != 264:
                continue
            listener._on_sample(r["t"], struct.unpack("<66f", bytes.fromhex(r["hex"])))
    runs = list(listener._completed)
    return runs[0] if runs else None


def make_dispatcher(telemetry):
    store = AccountStore(Path(r"D:\CODE\DR2\build\telemetry_wiring_accounts") / "u")
    d = RpcDispatcher(store, api_client=FakeApi())
    d.telemetry = telemetry
    d._telemetry_arm_wall = 0.0
    return d


def main():
    run = load_run()
    if run is None:
        print("no completed run in capture", file=sys.stderr)
        return 2

    segs4 = run.segment_times(4)
    check("run resolves 4 segments", segs4 is not None, True)
    raw_sum = round(sum(segs4), 3)
    print(f"  (raw German TT segments sum to {raw_sum}s)")

    # (a) a real run scales to the submitted official total exactly.
    d = make_dispatcher(FakeTelemetry(run))
    out = d._compute_stage_splits("evtS", 0, 0, time_ms=200000)   # 200 s
    check("a: returns 4 segments", len(out or []), 4)
    check("a: sums to official total", round(sum(out), 3), 200.0)

    # (b) a different official time rescales the same run.
    d = make_dispatcher(FakeTelemetry(run))
    out = d._compute_stage_splits("evtS", 0, 0, time_ms=180000)   # 180 s
    check("b: sums to 180s", round(sum(out), 3), 180.0)

    # (c) telemetry disabled -> None (no crash).
    d = make_dispatcher(None)
    check("c: no telemetry -> None", d._compute_stage_splits("evtS", 0, 0, 200000), None)

    # (d) no completed run available -> None.
    d = make_dispatcher(FakeTelemetry(None))
    check("d: no run -> None", d._compute_stage_splits("evtS", 0, 0, 200000), None)

    # (e) unknown track (no split count) -> None even with a run present.
    store = AccountStore(Path(r"D:\CODE\DR2\build\telemetry_wiring_accounts") / "u")
    d = RpcDispatcher(store, api_client=FakeApi())
    d.telemetry = FakeTelemetry(run)
    d._telemetry_arm_wall = 0.0
    # point the event at an unmapped track so _stage_split_profile returns None
    d.api_client.events = {"evtS": {"stages": [{"track_id": 1}]}}
    check("e: unmapped track -> None", d._compute_stage_splits("evtS", 0, 0, 200000), None)

    print(f"\n{'ALL PASS' if FAILS == 0 else str(FAILS) + ' FAILURE(S)'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
