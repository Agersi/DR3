"""Analyze a capture_dr2_udp.py JSONL file: field semantics + run segmentation.

Decodes every 264-byte datagram (66 little-endian floats) and reports:
  - packet count / rate, distinct senders
  - field [1] stage clock span, field [2] distance min/max/trajectory
  - field [61] track-length values seen (the listener takes the latest valid)
  - every backward distance jump > 25 m (time + before/after values) — the
    signature has_wrap() rejects on
  - a replay of TelemetryListener._on_sample edge detection, listing every run
    it would complete (start/finish wall, max dist, track len used, wrap flag)
    so the output shows exactly what take_run_since() would claim.

Usage:
    python tools\analyze_dr2_udp.py <capture.jsonl> [--n 4]

--n previews segment_times(n) for each replayed run (default 4).
"""

import argparse
import json
import struct
import sys
from pathlib import Path

PACKET_SIZE = 264
NUM_FLOATS = PACKET_SIZE // 4
FMT = "<%df" % NUM_FLOATS
F_STAGE_TIME = 1
F_DIST = 2
F_SPEED = 7
F_TRACK_LEN = 61
START_THRESH = 10.0
FINISH_THRESH = 10.0
WRAP_THRESH = 25.0


def segment_times(samples, n, track_len):
    """Mirror of StageRun.segment_times on (clock, dist) pairs."""
    if n <= 0 or len(samples) < 2 or track_len <= 0:
        return None
    run = [(d, c) for c, d in samples]

    def crossing(target):
        for i in range(1, len(run)):
            d0, c0 = run[i - 1]
            d1, c1 = run[i]
            if d1 >= target >= d0 or (i == 1 and d0 >= target):
                if d1 == d0:
                    return c1
                f = max(0.0, min(1.0, (target - d0) / (d1 - d0)))
                return c0 + (c1 - c0) * f
        return run[-1][1]

    bounds = [track_len * i / n for i in range(n + 1)]
    cross = [crossing(b) for b in bounds]
    cross[0] = 0.0
    segs = [cross[i + 1] - cross[i] for i in range(n)]
    if any(t < 0 for t in segs):
        return None
    return segs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", type=Path)
    ap.add_argument("--n", type=int, default=4, help="preview segment count")
    ap.add_argument("--window", nargs=2, type=float, metavar=("T0", "T1"),
                    help="dump every packet in [T0, T1] (capture-relative seconds) "
                         "at full rate: dist/clock/speed/len61")
    args = ap.parse_args()

    if args.window:
        t0w, t1w = args.window
        base = None
        with args.file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                hx = obj.get("hex", "")
                if len(hx) < PACKET_SIZE * 2:
                    continue
                if base is None:
                    base = obj["t"]
                t = obj["t"] - base
                if t < t0w or t > t1w:
                    continue
                v = struct.unpack(FMT, bytes.fromhex(hx)[:PACKET_SIZE])
                print(f"t={t:9.3f}  dist={v[F_DIST]:10.2f}  clock={v[F_STAGE_TIME]:9.3f}  "
                      f"speed={v[F_SPEED]:7.2f}  len61={v[F_TRACK_LEN]:10.2f}")
        return 0

    packets = []
    with args.file.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            hx = obj.get("hex", "")
            if len(hx) < PACKET_SIZE * 2:
                continue
            vals = struct.unpack(FMT, bytes.fromhex(hx)[:PACKET_SIZE])
            packets.append((obj["t"], vals))

    if not packets:
        print(f"no decodable packets in {args.file}")
        return 1

    t0 = packets[0][0]
    tn = packets[-1][0]
    n = len(packets)
    print(f"packets: {n}  span {tn - t0:.1f}s  rate {n / (tn - t0):.1f} Hz")

    clocks = [v[F_STAGE_TIME] for _, v in packets]
    dists = [v[F_DIST] for _, v in packets]
    tlens = sorted({round(v[F_TRACK_LEN], 1) for _, v in packets})
    print(f"field[1] stage clock: min={min(clocks):.2f} max={max(clocks):.2f}")
    print(f"field[2] distance:    min={min(dists):.2f} max={max(dists):.2f}")
    shown = tlens[:12]
    print(f"field[61] track len values ({len(tlens)} distinct): {shown}"
          + (" ..." if len(tlens) > 12 else ""))

    # Track-length field transitions (the listener keeps the latest valid value).
    trans = []
    prev_tl = None
    for t, v in packets:
        tl = round(v[F_TRACK_LEN], 1)
        if tl != prev_tl:
            trans.append((round(t - t0, 2), prev_tl, tl))
            prev_tl = tl
    print(f"field[61] transitions ({len(trans)}):")
    for tt in trans[:40]:
        print(f"  t={tt[0]:8.2f}s  {tt[1]} -> {tt[2]}")

    # Idle spans (all-zero samples = car parked, e.g. service park).
    idle = []
    i = 0
    while i < n:
        if all(packets[i][1][k] == 0.0 for k in (F_STAGE_TIME, F_DIST, F_SPEED)):
            j = i
            while j < n and all(packets[j][1][k] == 0.0
                               for k in (F_STAGE_TIME, F_DIST, F_SPEED)):
                j += 1
            if j - i >= 5:
                idle.append((round(packets[i][0] - t0, 1), round(packets[j - 1][0] - t0, 1)))
            i = j
        else:
            i += 1
    print(f"all-zero idle spans (>=5 pkts): {len(idle)}")
    for s in idle[:20]:
        print(f"  t={s[0]:8.1f}s .. {s[1]:8.1f}s  ({s[1] - s[0]:.1f}s)")

    # Backward jumps (the has_wrap signature) + zoomed context around each.
    jump_idx = []
    for i in range(1, n):
        d0 = packets[i - 1][1][F_DIST]
        d1 = packets[i][1][F_DIST]
        if d0 - d1 > WRAP_THRESH:
            jump_idx.append(i)
    print(f"backward jumps > {WRAP_THRESH} m: {len(jump_idx)}")
    for i in jump_idx[:20]:
        t = packets[i][0] - t0
        d0 = packets[i - 1][1][F_DIST]
        d1 = packets[i][1][F_DIST]
        print(f"  t={t:8.2f}s  dist {d0:.1f} -> {d1:.1f}")
        lo = max(0, i - 6)
        hi = min(n, i + 7)
        for k in range(lo, hi):
            tk, vk = packets[k]
            mark = " <==" if k == i else ""
            print(f"    t={tk - t0:8.2f}s  dist={vk[F_DIST]:9.1f}  "
                  f"clock={vk[F_STAGE_TIME]:8.2f}  speed={vk[7]:6.1f}  "
                  f"len61={vk[F_TRACK_LEN]:9.1f}{mark}")

    # Distance trajectory, downsampled to ~40 points.
    step = max(1, n // 40)
    traj = [(round(packets[i][0] - t0, 1), round(packets[i][1][F_DIST], 0),
             round(packets[i][1][F_STAGE_TIME], 1)) for i in range(0, n, step)]
    print("trajectory (t_s, dist_m, clock_s):")
    for row in traj:
        print(f"  t={row[0]:8.1f}  dist={row[1]:9.0f}  clock={row[2]:8.1f}")

    # Replay the listener's edge detection.
    runs = []
    buffer = None
    run_start = 0.0
    prev_at_start = False
    prev_at_finish = False
    track_len = 0.0
    for t, v in packets:
        tl = v[F_TRACK_LEN]
        if 100.0 < tl < 200000.0:
            track_len = tl
        dist = v[F_DIST]
        clock = v[F_STAGE_TIME]
        at_start = dist < START_THRESH
        at_finish = (track_len > 0.0) and (dist > track_len - FINISH_THRESH)
        if at_start and not prev_at_start:
            buffer = [(clock, dist)]
            run_start = t
        elif buffer is not None:
            buffer.append((clock, dist))
        if at_finish and not prev_at_finish and buffer is not None:
            wraps = any(buffer[i - 1][1] - buffer[i][1] > WRAP_THRESH
                        for i in range(1, len(buffer)))
            runs.append({
                "start": round(run_start - t0, 2),
                "finish": round(t - t0, 2),
                "samples": len(buffer),
                "max_dist": round(max(d for _, d in buffer), 1),
                "track_len": round(track_len, 1),
                "clock_span": round(clock - buffer[0][0], 1),
                "wrap": wraps,
                "segs": segment_times(buffer, args.n, track_len),
            })
            buffer = None
        prev_at_start = at_start
        prev_at_finish = at_finish

    if buffer is not None:
        print(f"NOTE: {len(buffer)} samples still buffered at capture end "
              "(run never hit the finish edge)")

    print(f"\nruns the listener would complete: {len(runs)}")
    for i, r in enumerate(runs):
        segs = r.pop("segs")
        print(f"  run {i}: start t={r['start']}s finish t={r['finish']}s "
              f"samples={r['samples']} max_dist={r['max_dist']}m "
              f"track_len={r['track_len']}m clock_span={r['clock_span']}s "
              f"wrap={r['wrap']}")
        if segs:
            print(f"    segment_times({args.n}) = {[round(s, 2) for s in segs]} "
                  f"(sum {sum(segs):.2f}s)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
