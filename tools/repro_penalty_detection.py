"""Verify penalty detection by replaying a raw UDP capture through the live
TelemetryListener code path.

Feeds every packet of a tools/capture_dr2_udp.py dump into
dr2server.telemetry.TelemetryListener._on_sample (no socket involved), collects
the completed runs it segments, then applies the same formula as
dispatcher.Dispatcher._stage_penalty_ms (in-run clock jumps + report-time
excess over the finish clock, 4s jitter floor) and compares the totals with
the penalties known to have been applied in that session.

Usage:
    python tools\repro_penalty_detection.py <capture.jsonl> <captures_dir> \
        [--date YYYYMMDD] [--expect 10,60,60,45] [--tol 2.5]

    --date   defaults to the capture file's mtime date; selects the
             RaceNetChallenges.StageComplete captures in <captures_dir>.
    --expect comma-separated expected penalty seconds per stage (default
             10,60,60,45 — the 2026-11-20 Test Club session).
    --tol    allowed deviation per stage, seconds (default 2.5).
"""

import argparse
import json
import struct
import sys
import time
from pathlib import Path

DR3_DIR = Path(__file__).resolve().parent.parent / "dr3"
sys.path.insert(0, str(DR3_DIR))

from dr2server.telemetry import PENALTY_JUMP_S, TelemetryListener  # noqa: E402

PACKET_SIZE = 264
NUM_FLOATS = PACKET_SIZE // 4
FMT = "<%df" % NUM_FLOATS

REPORT_TIME_FLOOR_MS = int(PENALTY_JUMP_S * 1000)  # mirrors dispatcher._stage_penalty_ms


def load_packets(jsonl_path: Path):
    out = []
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            raw = bytes.fromhex(rec["hex"])
            if len(raw) != PACKET_SIZE:
                continue
            out.append((rec["t"], struct.unpack(FMT, raw)))
    return out


def load_stage_completes(captures_dir: Path, date: str):
    files = sorted(captures_dir.glob(f"{date}-*_RaceNetChallenges.StageComplete.json"))
    stages = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        b = d.get("decoded_body") or {}
        stages.append({"file": f.name, "stage": b.get("StageIndex"),
                       "time_s": b.get("StageTime")})
    stages.sort(key=lambda s: (s["stage"] is None, s["stage"]))
    return stages


def penalty_ms(run, reported_ms: int) -> tuple:
    """Mirror of dispatcher._stage_penalty_ms."""
    in_run_ms = int(round(run.penalty_s * 1000))
    report_time_ms = max(0, reported_ms - int(round(run.total_time_s * 1000)))
    if report_time_ms < REPORT_TIME_FLOOR_MS:
        report_time_ms = 0
    return in_run_ms + report_time_ms, in_run_ms, report_time_ms


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("captures_dir", type=Path)
    ap.add_argument("--date", default="")
    ap.add_argument("--expect", default="10,60,60,45")
    ap.add_argument("--tol", type=float, default=2.5)
    args = ap.parse_args()

    date = args.date or time.strftime("%Y%m%d",
                                      time.localtime(args.jsonl.stat().st_mtime))
    expect = [float(x) for x in args.expect.split(",") if x.strip()]

    packets = load_packets(args.jsonl)
    print(f"loaded {len(packets)} packets from {args.jsonl.name}")

    listener = TelemetryListener(port=0)
    for t, vals in packets:
        listener._on_sample(t, vals)

    runs = []
    while True:
        r = listener.take_latest_completed()
        if r is None:
            break
        runs.append(r)
    runs.sort(key=lambda r: r.finish_wall)
    stages = load_stage_completes(args.captures_dir, date)
    print(f"listener segmented {len(runs)} run(s); "
          f"{len(stages)} StageComplete capture(s) for {date}\n")

    if len(runs) != len(stages):
        print(f"FAIL: run/stage count mismatch ({len(runs)} vs {len(stages)})")
        return 1

    ok = True
    for i, st in enumerate(stages):
        run = runs[i]
        reported_ms = int(round(st["time_s"] * 1000))
        total_ms, in_run_ms, report_ms = penalty_ms(run, reported_ms)
        raw_ms = reported_ms - total_ms
        exp_s = expect[i] if i < len(expect) else None
        verdict = ""
        if exp_s is not None:
            got_s = total_ms / 1000.0
            verdict = "OK" if abs(got_s - exp_s) <= args.tol else "MISMATCH"
            ok = ok and verdict == "OK"
        print(f"Stage {i + 1} (StageIndex={st['stage']}): reported "
              f"{st['time_s']:.3f}s | finish clock {run.total_time_s:.3f}s | "
              f"in-run jumps {in_run_ms / 1000.0:.3f}s | report-time "
              f"{report_ms / 1000.0:.3f}s | TOTAL {total_ms / 1000.0:.3f}s"
              + (f" | expected ~{exp_s:.0f}s -> {verdict}" if exp_s is not None else "")
              + f" | raw driving {raw_ms / 1000.0:.3f}s")

    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
