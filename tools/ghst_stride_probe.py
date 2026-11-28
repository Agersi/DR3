#!/usr/bin/env python3
"""GHST record-size finder via constant-field periodicity.

DR2's 264-byte UDP car-state packet has field[61] = track length (m), which is
constant for a whole run. If every ghost sample stores that float, its exact
f32 bytes recur once per record -> the spacing between occurrences IS the
record size F. We also test the stage-time float and zero.

For each capture we print, for a few candidate constant floats, the occurrence
count of their 4-byte pattern and the gcd/spacing of the offsets. A clean
uniform spacing across many hits = the sample grid.

Usage: ghst_stride_probe.py [--dir CAPTURE_DIR]
"""
import base64
import glob
import json
import math
import os
import struct
import sys
import zlib
from fractions import Fraction

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "dr3"))
from dr2server.game_data import Track  # noqa: E402


def load_ghst(path):
    d = json.load(open(path, encoding="utf-8"))
    body = d["decoded_body"]
    blob = base64.b64decode(body["GhostData"]["blob_base64"])
    data = zlib.decompress(blob[8:])
    return data, body


def find_offsets(data, pat):
    out = []
    i = 0
    while True:
        i = data.find(pat, i)
        if i < 0:
            return out
        out.append(i)
        i += 1


def main():
    capdir = r"C:\Users\Agersi\AppData\Roaming\DR3\captures"
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] == "--dir":
            capdir = argv[i + 1]
            i += 2
        else:
            i += 1
    files = sorted(glob.glob(os.path.join(capdir, "*PostTime.json")))
    for path in files[:8]:
        data, body = load_ghst(path)
        st = float(body["StageTime"])
        tr = int(body["TrackModelId"])
        try:
            lm = Track(tr).length_km * 1000.0
        except ValueError:
            lm = None
        X = struct.unpack_from("<H", data, 10)[0]
        Z = struct.unpack_from("<H", data, 20)[0]
        cands = {}
        if lm is not None:
            cands["tracklen"] = struct.pack("<f", lm)
        cands["stagetime"] = struct.pack("<f", st)
        cands["zero"] = struct.pack("<f", 0.0)
        print("%s tr=%d st=%.3f len=%s X=%d Z=%d N=%d" %
              (os.path.basename(path)[:18], tr, st,
               ("%.0f" % lm) if lm else "?", X, Z, len(data)))
        for name, pat in cands.items():
            offs = find_offsets(data, pat)
            if not offs:
                print("   %-10s count=0" % name)
                continue
            gaps = [b - a for a, b in zip(offs, offs[1:])]
            gg = 0
            for x in gaps:
                gg = math.gcd(gg, x)
            uniq = sorted(set(gaps))[:8]
            print("   %-10s count=%-3d first=%d last=%d gcd=%s gaps~%s"
                  % (name, len(offs), offs[0], offs[-1], gg, uniq))


if __name__ == "__main__":
    main()
