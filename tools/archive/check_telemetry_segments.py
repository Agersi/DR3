#!/usr/bin/env python3
"""Replay a DR2 UDP telemetry capture through dr2server.telemetry to verify run
segmentation + split derivation.

Feeds every 264-byte packet of a capture (as produced by tools/capture_dr2_udp.py)
through TelemetryListener._on_sample in order — exactly the live receive path,
minus the socket — then reports each completed start->finish run and its
per-segment times for a given split count.

Usage:
    python tools/check_telemetry_segments.py CAPTURE_JSONL [--splits N]

Default split count is 4 (the 900s-limit rally stage profile). Expected on the
German TT capture (build/dr2_udp_captures/splits-probe.jsonl): one run with
segments ~[59.2, 47.9, 46.6, 42.7] summing to ~196.4 s.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.telemetry import TelemetryListener  # noqa: E402


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("capture", type=Path)
    ap.add_argument("--splits", type=int, default=4)
    args = ap.parse_args()

    pkts = load_packets(args.capture)
    if not pkts:
        print("no 264-byte packets in capture", file=sys.stderr)
        return 2

    listener = TelemetryListener(port=0)
    for t, vals in pkts:
        listener._on_sample(t, vals)

    runs = list(listener._completed)
    print(f"packets={len(pkts)}  completed_runs={len(runs)}")
    if not runs:
        print("no completed start->finish run found", file=sys.stderr)
        return 1

    ok = True
    for idx, run in enumerate(runs):
        segs = run.segment_times(args.splits)
        n = len(run.samples)
        print(f"\nrun #{idx + 1}: {n} samples  "
              f"track_len={run.track_length_m:.1f}m  total_clock={run.total_time_s:.3f}s")
        if segs is None:
            print("  segment_times -> None (insufficient samples)")
            ok = False
            continue
        print("  segments (" + str(args.splits) + "): " + ", ".join(f"{s:.3f}" for s in segs))
        print(f"  sum(segments)={sum(segs):.3f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
