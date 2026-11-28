#!/usr/bin/env python3
"""GHST numeric-field correlation probe.

For every PostTime capture, extracts the run-varying header fields and prints
them next to StageTime / track length so correlations (sample counts, rates,
checksums) can be read off.

Usage: ghst_fields_probe.py [--dir CAPTURE_DIR]
"""
import glob
import json
import os
import struct
import sys
import zlib
import base64

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "dr3"))
from dr2server.game_data import Track  # noqa: E402


def load_ghst(path):
    d = json.load(open(path, encoding="utf-8"))
    body = d["decoded_body"]
    blob = base64.b64decode(body["GhostData"]["blob_base64"])
    data = zlib.decompress(blob[8:])
    return data, body


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def main():
    capdir = r"C:\Users\Agersi\AppData\Roaming\DR3\captures"
    if "--dir" in sys.argv:
        capdir = sys.argv[sys.argv.index("--dir") + 1]
    files = sorted(glob.glob(os.path.join(capdir, "*PostTime.json")))
    print("%-18s %-4s %-5s %-7s %-6s %-6s %-6s %-6s %-6s | %-7s %-7s %-7s %-7s | %s"
          % ("file", "tr", "vid", "st", "N", "X@10", "Z@20", "V@40", "ms@85",
             "g1@67", "g2@71", "g3@75", "g4@79", "len_m"))
    for path in files:
        data, body = load_ghst(path)
        st = float(body["StageTime"])
        tr = int(body["TrackModelId"])
        try:
            t = Track(tr)
            lm = "%.0f" % (t.length_km * 1000.0)
        except ValueError:
            lm = "?"
        print("%-18s %-4s %-5s %-7.3f %-6d %-6d %-6d %-6d %-6d | %-7d %-7d "
              "%-7d %-7d | %s"
              % (os.path.basename(path)[:18], tr, body.get("VehicleId"), st,
                 len(data), u16(data, 10), u16(data, 20), u16(data, 40),
                 u32(data, 85), u16(data, 67), u16(data, 71), u16(data, 75),
                 u16(data, 79), lm))


if __name__ == "__main__":
    main()
