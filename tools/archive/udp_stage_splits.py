#!/usr/bin/env python3
"""Derive real per-segment stage splits from a DR2 UDP telemetry capture.

The game's GetStageSplits answer (see tools/probe_upstream_splits.py --report)
carries the fastest clean time for a stage broken into N per-segment times that
sum to TotalTime. Upstream computes those server-side from the run's position
trajectory; the captured values are NON-uniform (e.g. 47.69, 49.66, ...), which
shows the split points are evenly spaced in DISTANCE along the stage and each
segment's time is whatever the car actually took to cover it.

DR2's UDP telemetry gives us exactly the inputs to reproduce that: field [2] is
distance-along-stage (m) and field [1] is the stage clock (s), sampled at ~50 Hz.
This tool reads a capture, finds each completed start->finish run, and computes
the time taken to cross each of N equal-distance segment boundaries.

Usage:
    python tools/udp_stage_splits.py CAPTURE_JSONL --track-id ID [--laps N]
                                       [--splits N] [--run LAST|BEST|ALL]
                                       [--emit OUT.json]

    --track-id  used to look up the stage's max-time limit (-> default split
                count) via dr3/dr2server/stage_limits.py; pass --splits to force.
    --run       LAST = last completed run, BEST = fastest clean run (default),
                ALL  = every completed run.
    --emit      also write the chosen run's answer in the upstream wire format:
                {"SplitsExist": true, "Splits": [{"Time": s}, ...], "TotalTime": s}

Field map (264-byte packet = 66 floats, little-endian):
    [1] stage clock (s)   [2] distance along stage (m)
    [4][5][6] position xyz (m)   [7] speed (m/s)   [61] track length (m)
"""
from __future__ import annotations

import argparse
import json
import statistics
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

F_STAGE_TIME = 1
F_DIST = 2
F_TRACK_LEN = 61

START_THRESH = 10.0   # m from the start line that counts as "at the start"
FINISH_THRESH = 10.0  # m short of track length that counts as "finished"


def load_packets(path: Path):
    out = []
    with open(path, encoding="utf-8") as fh:
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
            out.append((r["t"], struct.unpack("<66f", bytes.fromhex(r["hex"]))))
    return out


def track_length(pkts):
    vals = [d[F_TRACK_LEN] for _, d in pkts if d[F_TRACK_LEN] > 100.0]
    return statistics.median(vals) if vals else 0.0


def find_runs(pkts, L: float):
    """Return completed start->finish runs as lists of (t, dist, stage_time).

    A run begins on the transition INTO the start zone (dist < START_THRESH) and
    ends on the subsequent transition INTO the finish zone (dist > L-FINISH_THRESH).
    Edge detection (not per-packet region membership) so an idle-at-start or
    post-finish dwell does not spawn phantom runs.
    """
    if L <= 0:
        return []
    spans = []
    pending_start = None
    prev_at_start = False
    prev_at_finish = False
    for i, (t, d) in enumerate(pkts):
        dist = d[F_DIST]
        at_start = dist < START_THRESH
        at_finish = dist > L - FINISH_THRESH
        if at_start and not prev_at_start:
            pending_start = i
        if at_finish and not prev_at_finish and pending_start is not None:
            spans.append((pending_start, i))
            pending_start = None
        prev_at_start = at_start
        prev_at_finish = at_finish
    runs = []
    for s, e in spans:
        run = [(pkts[j][0], pkts[j][1][F_DIST], pkts[j][1][F_STAGE_TIME]) for j in range(s, e + 1)]
        if len(run) >= 2:
            runs.append(run)
    return runs


def crossing_stage_time(run, target_dist: float) -> float:
    """Stage clock when the car first reaches target_dist (linear interpolation)."""
    for i in range(1, len(run)):
        d0, d1 = run[i - 1][1], run[i][1]
        if d1 >= target_dist >= d0 or (i == 1 and d0 >= target_dist):
            if d1 == d0:
                return run[i][2]
            f = (target_dist - d0) / (d1 - d0)
            f = max(0.0, min(1.0, f))
            return run[i - 1][2] + (run[i][2] - run[i - 1][2]) * f
    return run[-1][2]


def segment_times(run, n: int, L: float):
    """Per-segment times for N equal-distance segments.

    The stage clock [1] is a real-time counter that starts at 0 at stage start
    (pre-stage) and runs continuously; the car crosses the start line (dist=0)
    only after a ~10 s pre-stage delay, so the official total (clock at finish)
    includes that delay. To make the segments sum to the official total (the
    wire-format invariant: Splits sum ~= TotalTime), the first boundary is
    anchored at clock=0 rather than at the dist=0 crossing time.
    """
    bounds = [L * i / n for i in range(n + 1)]
    cross = [crossing_stage_time(run, b) for b in bounds]
    cross[0] = 0.0  # official stage-clock start (includes pre-stage in segment 1)
    segs = [cross[i + 1] - cross[i] for i in range(n)]
    return segs, cross


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("capture", type=Path)
    ap.add_argument("--track-id", type=int, default=0)
    ap.add_argument("--laps", type=int, default=None)
    ap.add_argument("--splits", type=int, default=None,
                    help="force split count (default: from stage time limit)")
    ap.add_argument("--run", choices=("LAST", "BEST", "ALL"), default="BEST")
    ap.add_argument("--emit", type=Path, default=None)
    args = ap.parse_args()

    pkts = load_packets(args.capture)
    if not pkts:
        print("no 264-byte packets in capture", file=sys.stderr)
        return 2
    L = track_length(pkts)
    print(f"packets={len(pkts)}  track_length=[61]={L:.1f} m")

    n = args.splits
    if n is None:
        from dr2server.stage_limits import split_count_for_limit, stage_limit_ms
        limit = stage_limit_ms(args.track_id, laps=args.laps) if args.track_id else 0
        n = split_count_for_limit(limit) if limit else None
        print(f"track_id={args.track_id} limit={limit}ms -> splits={n}")
    if not n:
        print("could not determine split count; pass --splits N", file=sys.stderr)
        return 2

    runs = find_runs(pkts, L)
    print(f"completed runs found: {len(runs)}")
    if not runs:
        print("no completed start->finish run in this capture", file=sys.stderr)
        return 1

    def describe(run):
        segs, cross = segment_times(run, n, L)
        total = cross[-1] - cross[0]
        st0, stN = run[0][2], run[-1][2]
        return segs, total, st0, stN

    chosen = []
    if args.run == "ALL":
        chosen = list(enumerate(runs))
    elif args.run == "LAST":
        chosen = [(len(runs) - 1, runs[-1])]
    else:  # BEST = fastest clean (smallest total stage-clock span)
        best_i, best_v = 0, None
        for i, run in enumerate(runs):
            _, total, _, _ = describe(run)
            if best_v is None or total < best_v:
                best_i, best_v = i, total
        chosen = [(best_i, runs[best_i])]

    for idx, run in chosen:
        segs, total, st0, stN = describe(run)
        print(f"\nrun #{idx + 1}: {len(run)} pkts  stage_clock start={st0:.3f} finish={stN:.3f}")
        print(f"  segment times ({n}): " + ", ".join(f"{s:.3f}" for s in segs))
        print(f"  sum(segments)={sum(segs):.3f}s   total(span start->finish)={total:.3f}s")

    # Emit the upstream wire format for the (first) chosen run.
    if args.emit:
        idx, run = chosen[0]
        segs, total, st0, stN = describe(run)
        payload = {
            "SplitsExist": True,
            "Splits": [{"Time": round(s, 3)} for s in segs],
            "TotalTime": round(sum(segs), 3),
        }
        args.emit.parent.mkdir(parents=True, exist_ok=True)
        args.emit.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote {args.emit} (run #{idx + 1}, TotalTime={payload['TotalTime']}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
