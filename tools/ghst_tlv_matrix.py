#!/usr/bin/env python3
"""GHST TLV section extractor: walks the [tag][len][payload] records in the
header of every PostTime capture and prints a per-tag value matrix so tag
meanings can be identified (track id, vehicle id, times, checksums...).

Usage: ghst_tlv_matrix.py [--dir CAPTURE_DIR] [--start OFF]
"""
import base64
import glob
import json
import os
import struct
import sys
import zlib


def load_ghst(path):
    d = json.load(open(path, encoding="utf-8"))
    body = d["decoded_body"]
    blob = base64.b64decode(body["GhostData"]["blob_base64"])
    data = zlib.decompress(blob[8:])
    return data, body


def walk_tlv(data, start):
    off = start
    recs = {}
    order = []
    while off + 2 <= len(data):
        tag = data[off]
        ln = data[off + 1]
        if ln == 0 or off + 2 + ln > len(data):
            break
        payload = data[off + 2:off + 2 + ln]
        recs[tag] = payload
        order.append(tag)
        off += 2 + ln
    return recs, order, off


def main():
    argv = sys.argv[1:]
    capdir = r"C:\Users\Agersi\AppData\Roaming\DR3\captures"
    start = 59
    i = 0
    while i < len(argv):
        if argv[i] == "--dir":
            capdir = argv[i + 1]
            i += 2
        elif argv[i] == "--start":
            start = int(argv[i + 1])
            i += 2
        else:
            i += 1
    files = sorted(glob.glob(os.path.join(capdir, "*PostTime.json")))
    rows = []
    for path in files:
        data, body = load_ghst(path)
        recs, order, end = walk_tlv(data, start)
        row = {
            "file": os.path.basename(path)[:15],
            "tr": body.get("TrackModelId"),
            "vid": body.get("VehicleId"),
            "st": float(body.get("StageTime", 0)),
            "N": len(data),
        }
        for tag in order:
            p = recs[tag]
            if len(p) == 4:
                u32 = struct.unpack_from("<I", p)[0]
                f32 = struct.unpack_from("<f", p)[0]
                row["t%02x" % tag] = "u:%d|f:%.5g" % (u32, f32)
            elif len(p) == 8:
                row["t%02x" % tag] = "u64:%d" % struct.unpack_from("<Q", p)[0]
            else:
                row["t%02x" % tag] = p.hex()
        rows.append((row, order))
    # print header
    tags = []
    for _, order in rows:
        for t in order:
            if t not in tags:
                tags.append(t)
    cols = ["file", "tr", "vid", "st", "N"] + ["t%02x" % t for t in tags]
    print(" | ".join(cols))
    for row, _ in rows:
        print(" | ".join(str(row.get(c, "")) for c in cols))


if __name__ == "__main__":
    main()
