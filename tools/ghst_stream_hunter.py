#!/usr/bin/env python3
"""GHST sample-stream hunter.

Scans the decompressed GHST payload for the per-sample position stream by
testing two hypotheses at every 4-byte-aligned start offset:

  ABS   : consecutive f32 triples (x, y, z) are absolute positions;
          total path length should ~= catalog track length.
  DELTA : consecutive f32 triples are deltas; integrated running sum gives
          positions; same path-length check.

Sample counts tested: X (u16@10), Z (u16@20) and a few neighbours. Prints the
best-scoring (start, count, mode) candidates per capture so the true layout
can be read off.

Usage: ghst_stream_hunter.py [--dir CAPTURE_DIR] [--max-start N]
"""
import base64
import glob
import json
import math
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "dr3"))
from dr2server.game_data import Track  # noqa: E402


def load_ghst(path):
    d = json.load(open(path, encoding="utf-8"))
    body = d["decoded_body"]
    blob = base64.b64decode(body["GhostData"]["blob_base64"])
    data = zlib.decompress(blob[8:])
    return data, body


def path_len(data, start, count, mode):
    """Total path length over `count` f32 triples starting at `start`.
    Returns (length, ok) where ok=False if any sample is implausible."""
    total = 0.0
    px = py = pz = 0.0
    for k in range(count):
        off = start + k * 12
        if off + 12 > len(data):
            return None, False
        x, y, z = struct.unpack_from("<fff", data, off)
        if not all(math.isfinite(v) and abs(v) < 50000 for v in (x, y, z)):
            return None, False
        if mode == "abs":
            if k > 0:
                total += math.hypot(x - px, y - py, z - pz)
            px, py, pz = x, y, z
        else:  # delta
            px += x
            py += y
            pz += z
            if k > 0:
                total += math.hypot(x, y, z)
            if not all(math.isfinite(v) and abs(v) < 50000 for v in (px, py, pz)):
                return None, False
    return total, True


def main():
    capdir = r"C:\Users\Agersi\AppData\Roaming\DR3\captures"
    max_start = 600
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] == "--dir":
            capdir = argv[i + 1]
            i += 2
        elif argv[i] == "--max-start":
            max_start = int(argv[i + 1])
            i += 2
        else:
            i += 1
    files = sorted(glob.glob(os.path.join(capdir, "*PostTime.json")))
    for path in files:
        data, body = load_ghst(path)
        st = float(body["StageTime"])
        tr = int(body["TrackModelId"])
        try:
            lm = Track(tr).length_km * 1000.0
        except ValueError:
            continue
        X = struct.unpack_from("<H", data, 10)[0]
        Z = struct.unpack_from("<H", data, 20)[0]
        counts = sorted({X, Z, X - 1, X + 1, Z - 1, Z + 1})
        best = []
        for start in range(156, min(max_start, len(data) - 48), 4):
            for mode in ("abs", "delta"):
                for c in counts:
                    L, ok = path_len(data, start, c, mode)
                    if not ok:
                        continue
                    err = abs(L - lm) / lm
                    best.append((err, start, mode, c, L))
        best.sort()
        print("%s tr=%d st=%.3f len=%.0f X=%d Z=%d" %
              (os.path.basename(path)[:18], tr, st, lm, X, Z))
        for err, start, mode, c, L in best[:6]:
            print("   err=%5.1f%%  S=%3d %-6s count=%4d pathL=%.0f"
                  % (err * 100, start, mode, c, L))


if __name__ == "__main__":
    main()
