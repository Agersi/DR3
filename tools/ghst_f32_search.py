#!/usr/bin/env python3
"""Search GHST sample stream for absolute f32 position channels matching UDP positions.

Usage: ghst_f32_search.py CAPTURE_JSON UDP_JSONL
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
    return data, body


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
    resets = []
    prev = None
    for i, (t, d) in enumerate(pkts):
        if prev is not None and prev > 5 and d[1] < 1.0:
            mx = max((d2[1] for _, d2 in pkts[i:i + 12000]), default=0)
            if mx > 30:
                resets.append(i)
        prev = d[1]
    if not resets:
        return None
    start = resets[-1]
    best_i, best_v = start, -1.0
    for i in range(start, len(pkts)):
        d = pkts[i][1]
        if d[1] > best_v:
            best_i, best_v = i, d[1]
        if i > start + 5 and d[1] < best_v * 0.5:
            break
    return pkts[start:best_i + 1], best_v


def resample_equal_dist(attempt, n_pts):
    pts = [(d[4], d[5], d[6]) for _, d in attempt]
    cum = [0.0]
    for i in range(1, len(pts)):
        dx = pts[i][0] - pts[i - 1][0]
        dy = pts[i][1] - pts[i - 1][1]
        dz = pts[i][2] - pts[i - 1][2]
        cum.append(cum[-1] + math.sqrt(dx * dx + dy * dy + dz * dz))
    total = cum[-1]
    step = total / (n_pts - 1)
    out, j = [], 0
    for k in range(n_pts):
        target = k * step
        while j < len(cum) - 2 and cum[j + 1] < target:
            j += 1
        span = cum[j + 1] - cum[j] or 1e-9
        f = (target - cum[j]) / span
        p0, p1 = pts[j], pts[j + 1]
        out.append(tuple(p0[a] + (p1[a] - p0[a]) * f for a in range(3)))
    return out, total


def main():
    data, body = load_ghst(sys.argv[1])
    N = len(data)
    X = struct.unpack_from("<H", data, 10)[0]
    T = body["StageTime"]
    print("N=%d X=%d StageTime=%.3f" % (N, X, T))

    pkts = load_udp(sys.argv[2])
    res = find_last_attempt(pkts)
    attempt, t_end = res
    pts, total_len = resample_equal_dist(attempt, X)
    print("track length %.1f m; start pos %s" % (total_len, tuple(round(v, 1) for v in pts[0])))

    # For each candidate (record_start S, record_size F, f32 alignment a), score how well the
    # f32 channel matches each UDP axis over the first K records (limit to avoid drift if F is wrong).
    K = 60  # only check first K records
    results = []
    for S in range(160, 400):
        for F in range(20, 50):
            if S + F * K > N:
                continue
            for a in range(0, min(4, F)):
                # precompute f32 values for the channel
                vals = []
                ok = True
                for i in range(K):
                    off = S + i * F + a
                    if off + 4 > N:
                        ok = False
                        break
                    vals.append(struct.unpack_from("<f", data, off)[0])
                if not ok:
                    continue
                for ax in range(3):
                    tol = 2.0  # meters
                    m = sum(1 for i in range(K) if abs(vals[i] - pts[i][ax]) < tol)
                    if m >= K * 0.8:
                        results.append((m, S, F, a, ax))
    results.sort(reverse=True)
    print("\nf32 absolute-position channel matches (score/60, need >=48):")
    if not results:
        print("  NONE with fixed record size — variable-length records or different encoding")
    for m, S, F, a, ax in results[:15]:
        print("  score=%d S=%d F=%d align=%d axis=%d" % (m, S, F, a, ax))

    # Also: scan the whole stream for the start position as f32 (any alignment) to locate sample section
    sx, sy, sz = pts[0]
    print("\nsearching stream for start position f32s (tol 3 m):")
    hits = {"x": [], "y": [], "z": []}
    for off in range(160, N - 4):
        v = struct.unpack_from("<f", data, off)[0]
        if abs(v - sx) < 3:
            hits["x"].append(off)
        if abs(v - sy) < 3:
            hits["y"].append(off)
        if abs(v - sz) < 3:
            hits["z"].append(off)
    for k in ("x", "y", "z"):
        print("  %s (%.1f): %d hits, first few: %s" % (k, {"x": sx, "y": sy, "z": sz}[k], len(hits[k]), hits[k][:10]))


if __name__ == "__main__":
    main()
