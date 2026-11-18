"""Byte-diff decompressed TuningSetup blobs from StageBegin captures.

Used by the spare-tyres wire investigation: run the game with identical
car/tuning/compound but different SpareTyre counts, then diff the blobs to
locate (or rule out) a spare encoding inside the 222-byte payload.

Usage:
  python tuning_blob_diff.py <capture.json> [more captures...]
  python tuning_blob_diff.py --window=HHMM-HHMM   (all StageBegins in range)
"""
import base64
import glob
import json
import os
import struct
import sys
import zlib

CAPDIR = r"C:\Users\Agersi\AppData\Roaming\DR3\captures"


def load(path=None, window=None):
    rows = []
    if path:
        files = list(path)
    else:
        files = sorted(glob.glob(os.path.join(CAPDIR, "*.json")))
    for f in files:
        ts = os.path.basename(f)[:15]
        if window:
            lo, hi = window.split("-")
            if not (lo <= ts[9:13] < hi):
                continue
        try:
            data = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            h = item.get("headers") or {}
            if h.get("X-EgoNet-Function") != "RaceNetChallenges.StageBegin":
                continue
            db = item.get("decoded_body") or {}
            tun = db.get("TuningSetup") or {}
            raw = base64.b64decode(tun.get("blob_base64", "")) if tun.get("blob_base64") else b""
            dec = zlib.decompress(raw[16:]) if len(raw) > 16 and raw[16:18] == b"\x78\x9c" else b""
            rows.append((ts, db.get("VehicleId"), db.get("StageIndex"),
                         db.get("TyreCompound"), db.get("TyresRemaining"), dec))
    rows.sort()
    return rows


def fmt(off, a, b):
    av, bv = a[off:off + 4], b[off:off + 4]
    fa = struct.unpack("<f", av)[0] if len(av) == 4 else float("nan")
    fb = struct.unpack("<f", bv)[0] if len(bv) == 4 else float("nan")
    return "%s(%9.5g) -> %s(%9.5g)" % (av.hex(), fa, bv.hex(), fb)


def main():
    args = sys.argv[1:]
    window = None
    paths = []
    for a in args:
        if a.startswith("--window="):
            window = a.split("=", 1)[1]
        else:
            paths.append(a)
    rows = load(paths or None, window)
    if not rows:
        print("no StageBegin captures found")
        return
    print("%-16s %5s %3s %3s %3s" % ("time", "veh", "stg", "cmp", "tyr"))
    for ts, v, s, c, t, dec in rows:
        print("%-16s %5s %3s %3s %3s  len=%d sha=%s b17=%d" % (
            ts, v, s, c, t, len(dec), zlib.crc32(dec) & 0xFFFFFFFF, dec[17] if len(dec) > 17 else -1))
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a, b = rows[i][5], rows[j][5]
            n = min(len(a), len(b))
            idx = [k for k in range(n) if a[k] != b[k]]
            extra = abs(len(a) - len(b))
            tag = "%s s%s vs %s s%s" % (rows[i][0][9:], rows[i][2], rows[j][0][9:], rows[j][2])
            if idx or extra:
                print("\n== %s : n_diff=%d ==" % (tag, len(idx) + extra))
                for k in idx:
                    print("  off %3d: %s" % (k, fmt(k, a, b)))
            else:
                print("\n== %s : IDENTICAL ==" % tag)


if __name__ == "__main__":
    main()
