"""Analyze a raw UDP telemetry capture against captured StageComplete requests.

Segments the raw packet stream (tools/capture_dr2_udp.py output) into stage runs
using the stage-clock field, detects in-run anomalies (car-reset teleports,
stationary repair stops), then compares each run's finish clock with the
StageTime reported by the game for that stage. A large positive delta means the
game baked a penalty into the reported StageTime; ~0 means the reported time is
the raw driving clock and any penalty must be server-driven.

Usage:
    python tools\analyze_penalty_test.py <capture.jsonl> <captures_dir> [--date YYYYMMDD]

    --date  defaults to the local date of the capture file's mtime.
"""

import argparse
import json
import struct
import sys
import time
from collections import Counter
from pathlib import Path

PACKET_SIZE = 264
NUM_FLOATS = PACKET_SIZE // 4
FMT = "<%df" % NUM_FLOATS
F_CLOCK, F_DIST, F_PX, F_PY, F_PZ, F_SPEED, F_TRACK = 1, 2, 4, 5, 6, 7, 61

CLOCK_RESET_S = 2.0      # clock drop larger than this starts a new run
JUMP_M = 30.0            # position jump between samples larger than this = teleport
STILL_SPEED = 0.5        # m/s; below this the car counts as stationary
STILL_MIN_S = 3.0        # stationary longer than this is reported


def load_samples(jsonl_path: Path):
    samples = []
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            raw = bytes.fromhex(rec["hex"])
            if len(raw) != PACKET_SIZE:
                continue
            f = struct.unpack(FMT, raw)
            samples.append((rec["t"], f[F_CLOCK], f[F_DIST],
                            (f[F_PX], f[F_PY], f[F_PZ]), f[F_SPEED], f[F_TRACK]))
    return samples


def segment_runs(samples):
    runs = []
    cur = None
    for t, clock, dist, pos, speed, track in samples:
        if cur is not None and clock < cur["clock_last"] - CLOCK_RESET_S:
            runs.append(cur)
            cur = None
        if cur is None:
            cur = {"t0": t, "t1": t, "clock0": clock, "clock_last": clock,
                   "n": 0, "speed_max": 0.0, "track": Counter(),
                   "jumps": [], "still": []}
        cur["t1"] = t
        cur["clock_last"] = clock
        cur["n"] += 1
        cur["speed_max"] = max(cur["speed_max"], speed)
        if track > 0:
            cur["track"][round(track)] += 1
        prev = cur.get("prev_pos")
        if prev is not None:
            dx, dy, dz = (a - b for a, b in zip(pos, prev))
            d = (dx * dx + dy * dy + dz * dz) ** 0.5
            if d > JUMP_M:
                cur["jumps"].append((t, round(d, 1), clock))
        cur["prev_pos"] = pos
    if cur is not None:
        runs.append(cur)

    return runs


def find_still_intervals(samples, run):
    out = []
    t0 = run["t0"]
    t1 = run["t1"]
    start = None
    clock_at_start = None
    prev_t = None
    for t, clock, dist, pos, speed, track in samples:
        if t < t0 or t > t1:
            continue
        if speed < STILL_SPEED:
            if start is None:
                start = t
                clock_at_start = clock
        else:
            if start is not None and (t - start) >= STILL_MIN_S:
                out.append((start, prev_t, round(clock_at_start, 1),
                            round(prev_t - start, 1)))
            start = None
        prev_t = t
    if start is not None and (prev_t - start) >= STILL_MIN_S:
        out.append((start, prev_t, round(clock_at_start, 1),
                    round(prev_t - start, 1)))
    return out


def load_stage_completes(captures_dir: Path, date: str):
    files = sorted(captures_dir.glob(f"{date}-*_RaceNetChallenges.StageComplete.json"))
    stages = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        b = d.get("decoded_body") or {}
        stages.append({
            "file": f.name,
            "stage": b.get("StageIndex"),
            "time_s": b.get("StageTime"),
        })
    stages.sort(key=lambda s: (s["stage"] is None, s["stage"]))
    return stages


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("captures_dir", type=Path)
    ap.add_argument("--date", default="", help="YYYYMMDD of the test session")
    args = ap.parse_args()

    import time
    date = args.date or time.strftime("%Y%m%d",
                                      time.localtime(args.jsonl.stat().st_mtime))
    samples = load_samples(args.jsonl)
    print(f"loaded {len(samples)} samples from {args.jsonl.name} "
          f"({samples[0][0]:.1f}s .. {samples[-1][0]:.1f}s)")

    runs = segment_runs(samples)
    stages = load_stage_completes(args.captures_dir, date)
    print(f"runs: {len(runs)}   StageComplete captures for {date}: {len(stages)}\n")

    if len(runs) < len(stages):
        print(f"WARNING: fewer runs ({len(runs)}) than stages ({len(stages)}) — "
              "last run(s) may be truncated or a stage had no telemetry\n")

    for i, st in enumerate(stages):
        r = runs[i] if i < len(runs) else None
        print(f"=== Stage {i + 1} (StageIndex={st['stage']}) — "
              f"reported StageTime = {st['time_s']:.3f}s")
        if r is None:
            print("    NO TELEMETRY RUN FOUND\n")
            continue
        finish_clock = r["clock_last"]
        delta = st["time_s"] - finish_clock
        track = r["track"].most_common(1)[0][0] if r["track"] else 0
        print(f"    run: {r['n']} samples, {r['t0']:.1f}s..{r['t1']:.1f}s "
              f"(wall {r['t1'] - r['t0']:.1f}s), clock {r['clock0']:.2f} -> "
              f"{finish_clock:.2f}s, max speed {r['speed_max']:.1f} m/s, "
              f"track len {track} m")
        print(f"    DELTA (reported - finish clock) = {delta:+.3f}s")
        if r["jumps"]:
            for t, d, c in r["jumps"]:
                print(f"    JUMP at t={t:.1f}s (clock {c:.1f}s): "
                      f"{d} m position discontinuity (car reset?)")
        still = find_still_intervals(samples, r)
        for s0, s1, c, dur in still:
            print(f"    STATIONARY clock~{c}s: t={s0:.1f}..{s1:.1f}s "
                  f"({dur}s stopped — repair stop?)")
        if abs(delta) < 0.5 and not r["jumps"] and not still:
            print("    (clean run, no anomaly)")
        print()

    print("Interpretation: delta ~ +10/+60/+60/+45s on the penalty stages =>")
    print("penalties are baked into StageTime by the client. delta ~ 0 everywhere")
    print("=> reported time is raw; penalties must be server-driven (Progress).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
