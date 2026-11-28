#!/usr/bin/env python3
"""GHST ghost-blob structure probe (format reverse-engineering aid).

Decompresses the GhostData blob(s) of one or more DR3 captures and probes the
post-header layout: header field dump, byte entropy by offset window, and a
periodicity scan that looks for a fixed record size F in the sample stream.

Usage:
  ghst_structure_probe.py CAPTURE_JSON [CAPTURE_JSON ...] [--no-periodic]

Ground truth printed per capture: StageTime + catalog track length (meters),
so a candidate record layout can be checked against path-length totals.
"""
import base64
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


def header_dump(data):
    N = len(data)
    u = {}
    for off in range(4, 96, 2):
        u[off] = struct.unpack_from("<H", data, off)[0]
    print("  N=%d" % N)
    print("  magic=%s ver=%u size@4=%u (N-8=%d)" %
          (data[:4], data[4], struct.unpack_from("<I", data, 4)[0], N - 8))
    for off in range(8, 96, 2):
        print("  u16@%3d = %6d" % (off, u[off]))


def entropy_window(data, start, end):
    """Byte-frequency entropy per 64-byte window — flat stream shows low,
    regular structure; random-ish zlib tail shows high."""
    out = []
    for s in range(start, min(end, len(data)), 64):
        w = data[s:s + 64]
        freq = {}
        for b in w:
            freq[b] = freq.get(b, 0) + 1
        e = 0.0
        for c in freq.values():
            p = c / len(w)
            e -= p * math.log2(p)
        out.append((s, round(e, 2)))
    return out


def periodicity(data, s_lo=100, s_hi=400, f_lo=8, f_hi=64):
    """For each (start S, record F), score how 'delta-like' the channel looks:
    bytes at S + k*F should be small (deltas) or form a smooth sequence.
    Returns top candidates by run-length of plausible values."""
    N = len(data)
    best = []
    for S in range(s_lo, s_hi):
        for F in range(f_lo, f_hi):
            nrec = (N - S) // F
            if nrec < 50:
                continue
            # plausibility: fraction of records where all bytes are 'quiet'
            # (|byte| <= 120 as signed) — delta streams are mostly quiet.
            quiet = 0
            step = max(1, nrec // 400)
            checked = 0
            for k in range(0, nrec, step):
                rec = data[S + k * F:S + (k + 1) * F]
                if all(abs(b - 128) <= 120 for b in rec):
                    quiet += 1
                checked += 1
            score = quiet / checked
            best.append((score, S, F, nrec))
    best.sort(reverse=True)
    return best[:15]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    no_periodic = "--no-periodic" in sys.argv
    for path in args:
        data, body = load_ghst(path)
        st = float(body["StageTime"])
        tr = int(body["TrackModelId"])
        try:
            t = Track(tr)
            length_m = t.length_km * 1000.0
            name = t.display_name
        except ValueError:
            length_m, name = None, "?"
        print("=" * 78)
        print("%s  track=%s (%s) st=%.3fs length=%sm" %
              (path.split("\\")[-1], tr, name, st,
               "%.0f" % length_m if length_m else "?"))
        header_dump(data)
        print("  entropy windows:")
        for s, e in entropy_window(data, 96, 512):
            print("    off %4d: %.2f" % (s, e))
        if not no_periodic:
            print("  top periodicity candidates (score, S, F, nrec):")
            for score, S, F, nrec in periodicity(data):
                print("    %.3f  S=%3d F=%2d nrec=%5d  Hz(if st)=%.2f" %
                      (score, S, F, nrec, nrec / st))


if __name__ == "__main__":
    main()
