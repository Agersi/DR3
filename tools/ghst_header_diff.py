#!/usr/bin/env python3
"""GHST header diff: decompress GhostData blobs from several captures and print
a per-offset table of which bytes are identical / varying across the set, so
track-constant vs run-varying header fields can be identified.

Usage: ghst_header_diff.py CAPTURE_JSON [CAPTURE_JSON ...] [--window END]
"""
import base64
import json
import sys
import zlib


def load_ghst(path):
    d = json.load(open(path, encoding="utf-8"))
    body = d["decoded_body"]
    blob = base64.b64decode(body["GhostData"]["blob_base64"])
    data = zlib.decompress(blob[8:])
    return data, body


def main():
    argv = sys.argv[1:]
    window = 320
    args = []
    i = 0
    while i < len(argv):
        if argv[i] == "--window":
            window = int(argv[i + 1])
            i += 2
        else:
            args.append(argv[i])
            i += 1
    blobs = []
    for path in args:
        data, body = load_ghst(path)
        blobs.append((path.split("\\")[-1], data, float(body["StageTime"]),
                      body["TrackModelId"]))
    n = min(len(b[1]) for b in blobs)
    lim = min(window, n)
    print("offset | " + " | ".join(b[0][:22] for b in blobs))
    prev_same = True
    for off in range(8, lim):
        vals = [b[1][off] for b in blobs]
        same = len(set(vals)) == 1
        mark = "=" if same else "*"
        if same != prev_same:
            print("-" * 90)
            prev_same = same
        print("%4d %s | " % (off, mark) + " | ".join(
            "%02x(%d)" % (v, v) for v in vals))


if __name__ == "__main__":
    main()
