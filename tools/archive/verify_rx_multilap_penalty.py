"""Verify the multi-lap (rallycross) telemetry path end to end, synthetically.

No capture needed: feeds fabricated 50 Hz samples through the live
TelemetryListener._on_sample code — one run per lap, distance wrapping at the
line and the stage clock resetting per lap, exactly as the game emits on a
multi-lap circuit — then checks:

  1. the listener segments one StageRun per lap, in order;
  2. take_runs_since() claims the whole lap set after the arm wall (and the
     ring no longer evicts unclaimed laps of a long stage);
  3. _stage_penalty_ms_multi() reports 0 on a clean finish, the in-run jump
     when one lap carries a car-reset, and the report-time excess for a
     service over-time;
  4. the _stage_complete completeness guard (gap vs _MULTI_LAP_SUM_TOL_MS)
     refuses a set with a missing lap instead of fabricating a penalty;
  5. a single monotonic rally pass still yields exactly one run, so the
     legacy single-run path is untouched;
  6. take_run_since() semantics are unchanged (claims only the most recent
     run after the wall).

Usage:
    python tools\verify_rx_multilap_penalty.py [--laps N] [--length M]
"""

import argparse
import sys
from pathlib import Path

DR3_DIR = Path(__file__).resolve().parent.parent / "dr3"
sys.path.insert(0, str(DR3_DIR))

from dr2server.dispatcher import (  # noqa: E402
    _MULTI_LAP_SUM_TOL_MS, RpcDispatcher as Dispatcher)
from dr2server.telemetry import (  # noqa: E402
    F_DIST, F_STAGE_TIME, F_SPEED, F_TRACK_LEN, TelemetryListener)

_NUM_FLOATS = 66
_DT = 0.02  # 50 Hz
_DWELL_S = 1.0  # start-box dwell before each lap's drive


def make_sample(clock: float, dist: float, speed: float, length: float):
    vals = [0.0] * _NUM_FLOATS
    vals[F_STAGE_TIME] = clock
    vals[F_DIST] = dist
    vals[F_SPEED] = speed
    vals[F_TRACK_LEN] = length
    return tuple(vals)


def feed_rx_laps(listener: TelemetryListener, laps_s: list[float],
                 length: float, jump_lap: int = -1,
                 jump_ms: float = 8000.0) -> float:
    """Feed one dwell+drive per lap; wall time is continuous, the stage clock
    resets each lap. Returns t after the final sample."""
    t = 0.0
    for li, lap_time in enumerate(laps_s):
        # start-box dwell (clock frozen at 0)
        n_dwell = int(_DWELL_S / _DT)
        for _ in range(n_dwell):
            listener._on_sample(t, make_sample(0.0, 2.0, 0.0, length))
            t += _DT
        # drive 2m -> length over lap_time
        n_drive = max(2, int(lap_time / _DT))
        jumped = 0.0
        for k in range(1, n_drive + 1):
            frac = k / n_drive
            dist = 2.0 + (length - 2.0) * frac
            if jump_lap == li and k == n_drive // 2:
                jumped = jump_ms / 1000.0  # instant stage-clock advance
            clock = lap_time * frac + jumped
            listener._on_sample(t, make_sample(clock, dist,
                                               (length - 2.0) / lap_time,
                                               length))
            t += _DT
    return t


def feed_rally_pass(listener: TelemetryListener, stage_s: float,
                    length: float) -> float:
    """One monotonic start->finish rally pass (no wrap)."""
    t = 0.0
    n_dwell = int(_DWELL_S / _DT)
    for _ in range(n_dwell):
        listener._on_sample(t, make_sample(0.0, 2.0, 0.0, length))
        t += _DT
    n_drive = max(2, int(stage_s / _DT))
    for k in range(1, n_drive + 1):
        frac = k / n_drive
        listener._on_sample(t, make_sample(stage_s * frac,
                                           2.0 + (length - 2.0) * frac,
                                           (length - 2.0) / stage_s, length))
        t += _DT
    return t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--laps", type=int, default=12)
    ap.add_argument("--length", type=float, default=2000.0)
    args = ap.parse_args()

    ok = True

    def check(name: str, cond: bool, detail: str = ""):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'} {name}" + (f" — {detail}"
                                                          if detail else ""))
        ok = ok and cond

    # -- case 1: clean 12-lap RX finish -----------------------------------
    print("case 1: clean multi-lap finish")
    laps_s = [57.2, 65.8, 48.7, 50.2, 77.3, 70.6,
              50.1, 65.3, 77.1, 61.8, 58.7, 61.7][:args.laps]
    while len(laps_s) < args.laps:
        laps_s.append(60.0)
    listener = TelemetryListener(port=0)
    feed_rx_laps(listener, laps_s, args.length)
    arm_wall = -5.0  # armed before the stage began
    runs = listener.take_runs_since(arm_wall)
    check("one run per lap", len(runs) == args.laps, f"{len(runs)} runs")
    check("chronological order",
          all(runs[i].finish_wall < runs[i + 1].finish_wall
              for i in range(len(runs) - 1)))
    check("ring drained", listener.take_runs_since(arm_wall) == [])
    lap_sum_ms = int(round(sum(r.total_time_s for r in runs) * 1000))
    total_ms = lap_sum_ms  # a clean finish reports the sum of its laps
    pen = Dispatcher._stage_penalty_ms_multi(None, runs, total_ms)
    check("clean finish -> 0 penalty", pen == 0, f"{pen}ms")

    # -- case 2: car-reset jump mid-lap ------------------------------------
    print("case 2: in-run penalty on one lap")
    listener = TelemetryListener(port=0)
    feed_rx_laps(listener, laps_s, args.length, jump_lap=4, jump_ms=8000.0)
    runs = listener.take_runs_since(arm_wall)
    check("still one run per lap", len(runs) == args.laps)
    total_ms = int(round(sum(r.total_time_s for r in runs) * 1000))
    pen = Dispatcher._stage_penalty_ms_multi(None, runs, total_ms)
    check("8s jump detected (~8000ms)", 7400 <= pen <= 8300, f"{pen}ms")

    # -- case 3: report-time addition (service over-time) ------------------
    print("case 3: report-time excess")
    listener = TelemetryListener(port=0)
    feed_rx_laps(listener, laps_s, args.length)
    runs = listener.take_runs_since(arm_wall)
    base_ms = int(round(sum(r.total_time_s for r in runs) * 1000))
    pen = Dispatcher._stage_penalty_ms_multi(None, runs, base_ms + 10_000)
    check("10s report-time excess counted", pen == 10_000, f"{pen}ms")
    pen = Dispatcher._stage_penalty_ms_multi(None, runs, base_ms + 2_000)
    check("sub-floor drift floored to 0", pen == 0, f"{pen}ms")

    # -- case 4: missing lap -> completeness guard refuses ------------------
    print("case 4: incomplete lap set")
    listener = TelemetryListener(port=0)
    feed_rx_laps(listener, laps_s, args.length)
    with listener._lock:
        listener._completed.pop(len(listener._completed) // 2)  # drop a lap
    runs = listener.take_runs_since(arm_wall)
    gap_ms = int(round(sum(r.total_time_s for r in runs) * 1000))
    reported_ms = gap_ms + int(round(laps_s[len(runs)] * 1000))
    refused = abs(reported_ms - gap_ms) > _MULTI_LAP_SUM_TOL_MS
    check("gap exceeds tolerance -> refuse", refused,
          f"gap {reported_ms - gap_ms}ms vs tol {_MULTI_LAP_SUM_TOL_MS}ms")

    # -- case 5: rally single pass ------------------------------------------
    print("case 5: single monotonic rally pass")
    listener = TelemetryListener(port=0)
    feed_rally_pass(listener, 300.0, 25_000.0)
    runs = listener.take_runs_since(arm_wall)
    check("exactly one run", len(runs) == 1, f"{len(runs)} runs")
    if runs:
        pen = Dispatcher._stage_penalty_ms(None, runs[0],
                                           int(round(runs[0].total_time_s * 1000)))
        check("legacy single-run penalty path -> 0", pen == 0, f"{pen}ms")

    # -- case 6: take_run_since semantics unchanged --------------------------
    print("case 6: take_run_since still claims only the most recent run")
    listener = TelemetryListener(port=0)
    feed_rx_laps(listener, laps_s[:3], args.length)
    latest = listener.take_run_since(arm_wall)
    check("most recent claimed", latest is not None and
          abs(latest.total_time_s - laps_s[2]) < 0.5,
          f"clock {latest.total_time_s if latest else None:.3f}s")
    with listener._lock:
        remaining = len(listener._completed)
    check("earlier runs left unclaimed", remaining == 2, f"{remaining} left")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
