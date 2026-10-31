#!/usr/bin/env python3
"""GHST ghost-blob decode probe: correlate decompressed sample stream against a UDP capture of the same run.

Usage:
  ghst_decode_probe.py CAPTURE_JSON [UDP_JSONL]

CAPTURE_JSON : DR3 capture file whose decoded_body.GhostData holds the blob
UDP_JSONL    : optional tools/capture_dr2_udp.py output (66-float packets)
"""
import base64
import json
import math
import struct
import sys
import zlib


def load_ghst(path):
    d = json.load(open(path, encoding="utf-8"))
    body = d["decoded_body"]
    blob = base64.b64decode(body["GhostData"]["blob_base64"])
    data = zlib.decompress(blob[8:])
    hdr = {
        "stage_time": body.get("StageTime"),
        "track": body.get("TrackModelId"),
        "vehicle": body.get("VehicleId"),
        "N": len(data),
        "X": struct.unpack_from("<H", data, 10)[0],
    }
    return data, hdr


def autocorr(x, max_lag=64):
    n = len(x)
    mean = sum(x) / n
    c0 = sum((b - mean) ** 2 for b in x) or 1.0
    out = []
    for lag in range(1, max_lag + 1):
        s = sum((x[i] - mean) * (x[i + lag] - mean) for i in range(n - lag))
        out.append(s / c0)
    return out


def load_udp(path):
    pkts = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("len") != 264:
                continue
            pkts.append((r["t"], struct.unpack("<66f", bytes.fromhex(r["hex"]))))
    return pkts


def find_last_attempt(pkts):
    """Return packets of the last completed stage attempt: [1] rises from ~0 to its max."""
    resets = []
    prev = None
    for i, (t, d) in enumerate(pkts):
        if prev is not None and prev > 5 and d[1] < 1.0:
            # only keep resets that are followed by a substantial run
            mx = max((d2[1] for _, d2 in pkts[i:i + 12000]), default=0)
            if mx > 30:
                resets.append(i)
        prev = d[1]
    if not resets:
        return None
    start = resets[-1]
    best_i, best_v = start, -1.0
    for i in range(start, len(pkts)):
        t, d = pkts[i]
        if d[1] > best_v:
            best_i, best_v = i, d[1]
        if i > start + 5 and d[1] < best_v * 0.5:
            break
    return pkts[start:best_i + 1], best_v


def resample_equal_dist(attempt, n_pts):
    """Resample the attempt trajectory to n_pts equal-arc-length points. Returns list of (x,y,z)."""
    pts = [(d[4], d[5], d[6]) for _, d in attempt]
    cum = [0.0]
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        dz = pts[i][2] - pts[i - 1][2]
        cum.append(cum[-1] + math.sqrt(dx * dx + dy * dy + dz * dz))
    total = cum[-1]
    step = total / (n_pts - 1)
    out = []
    j = 0
    for k in range(n_pts):
        target = k * step
        while j < len(cum) - 2 and cum[j + 1] < target:
            j += 1
        span = cum[j + 1] - cum[j] or 1e-9
        f = (target - cum[j]) / span
        p0, p1 = pts[j], pts[j + 1]
        out.append((p0[0] + (p1[0] - p0[0]) * f,
                    p0[1] + (p1[1] - p0[1]) * f,
                    p0[2] + (p1[2] - p0[2]) * f))
    return out, total


def main():
    cap = sys.argv[1]
    data, hdr = load_ghst(cap)
    N, X = hdr["N"], hdr["X"]
    print("blob: track=%s vehicle=%s StageTime=%.3f N=%d X=%d rate=%.3f Hz" %
          (hdr["track"], hdr["vehicle"], hdr["stage_time"], N, X, X / hdr["stage_time"]))

    S0 = 204
    stream = data[S0:N]
    print("\nautocorrelation of sample stream (off %d..%d), top lags:" % (S0, N))
    ac = autocorr(stream, 64)
    for lag, v in sorted(enumerate(ac, 1), key=lambda p: -abs(p[1]))[:10]:
        print("  lag %2d: %+.3f" % (lag, v))

    if len(sys.argv) < 3:
        return
    pkts = load_udp(sys.argv[2])
    print("\nUDP packets: %d" % len(pkts))
    res = find_last_attempt(pkts)
    if not res:
        print("no attempt found")
        return
    attempt, t_end = res
    t0 = attempt[0][0]
    print("attempt: %d pkts, t=%.2f..%.2f, stage_time_max=%.3f" % (len(attempt), t0, attempt[-1][0], t_end))

    pts, total_len = resample_equal_dist(attempt, X)
    print("resampled to %d equal-dist points, track length %.1f m, step %.2f m" % (X, total_len, total_len / (X - 1)))

    # int8 decimeter deltas
    deltas = []
    for k in range(1, X):
        d = [round((pts[k][a] - pts[k - 1][a]) * 10) for a in range(3)]
        deltas.append(tuple(d))
    print("\nfirst 45 (dx,dy,dz) int8-dm deltas:")
    for k, d in enumerate(deltas[:45]):
        print("  s%3d: %+4d %+4d %+4d" % (k + 1, *d))

    # --- time-based resampling hypothesis (samples at fixed stage-time intervals) ---
    import bisect
    T = t_end
    tbase = attempt[0][0]          # udp time where [1] reset to 0
    tjump = None
    for t, d in attempt:
        if d[1] >= 9.9:
            tjump = t
            break
    t_arm = (tjump - 10.0) if tjump else tbase   # stage clock arm time ([1] has ~10s head start)
    p_reset = (attempt[0][1][4], attempt[0][1][5], attempt[0][1][6])  # x,y,z at reset
    ts = [t for t, _ in attempt]

    def pos_at_tau(tau):
        if tau < 10.0:
            return p_reset  # idle at start line
        t = t_arm + tau
        i = bisect.bisect_left(ts, t)
        if i <= 0:
            j = 0
        elif i >= len(ts):
            j = len(ts) - 1
        else:
            j = i - 1 if (t - ts[i - 1] < ts[i] - t) else i
        dd = attempt[j][1]
        return (dd[4], dd[5], dd[6])

    pts_t = [pos_at_tau(k * T / X) for k in range(X)]
    deltas_t = []
    for k in range(1, X):
        deltas_t.append(tuple(round((pts_t[k][a] - pts_t[k - 1][a]) * 10) for a in range(3)))

    # --- fingerprint window matching ---
    W = 30

    def best_window(seq):
        best_w, best_s = None, -1
        for w0 in range(0, len(seq) - W, 5):
            win = seq[w0:w0 + W]
            s = len(set(win)) - sum(1 for v in win if abs(v) < 5) / 10.0
            if s > best_s:
                best_s, best_w = s, w0
        return best_w

    candidates = []
    for mode_name, deltas_m in (("dist", deltas), ("time", deltas_t)):
        for scale in (0.1, 0.2):
            seqs = []
            for ax in range(3):
                for sgn in (1, -1):
                    seqs.append((ax, sgn, [sgn * round(d[ax] / scale) for d in deltas_m]))
            for ax, sgn, seq in seqs:
                w0 = best_window(seq)
                if w0 is None:
                    continue
                win = seq[w0:w0 + W]
                if len(set(win)) < 24:
                    continue
                # scan candidate stream positions for the window start
                for p in range(165, min(N - 400, 2300)):
                    if data[p] != (win[0] & 0xFF):
                        continue
                    pos = p
                    ok = True
                    for k in range(1, W):
                        lo, hi = pos + 42, min(pos + 50, N)
                        cands = [j for j in range(lo, hi) if data[j] == (win[k] & 0xFF)]
                        if not cands:
                            ok = False
                            break
                        pos = min(cands, key=lambda j: abs(j - (pos + 45.5)))
                    if ok:
                        candidates.append((mode_name, scale, ax, sgn, w0, p))

    print("\nfingerprint window matches (W=%d):" % W)
    if not candidates:
        print("  NONE — int8 delta hypothesis (scales 0.1/0.2 m) not supported")
    for c in candidates[:20]:
        mode_name, scale, ax, sgn, w0, p = c
        print("  mode=%-4s scale=%.1f axis=%d sign=%+d window_start_sample=%d stream_pos=%d" % c)


if __name__ == "__main__":
    main()
