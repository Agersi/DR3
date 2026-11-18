"""One-off: dump float fields of decompressed TuningSetup blobs for consecutive
StageBegin captures, to identify what the per-stage changing bytes are."""
import base64
import glob
import json
import os
import struct
import sys
import zlib

CAPDIR = r"C:\Users\Agersi\AppData\Roaming\DR3\captures"


def load_rows():
    rows = []
    for f in sorted(glob.glob(os.path.join(CAPDIR, "*.json"))):
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
            rows.append((os.path.basename(f)[:15], db.get("VehicleId"), db.get("StageIndex"),
                         db.get("TyreCompound"), db.get("TyresRemaining"), dec))
    rows.sort()
    return rows


def main():
    veh = int(sys.argv[1]) if len(sys.argv) > 1 else 470
    lo, hi = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (104, 200)
    rows = [r for r in load_rows() if r[1] == veh]
    print("floats at offsets %d..%d (step 4)" % (lo, hi))
    hdr = "%-16s %3s %3s " % ("time", "stg", "tyr")
    offs = list(range(lo, hi + 1, 4))
    for o in offs:
        hdr += "f%-4d" % o
    print(hdr)
    for ts, v, stg, cmp_, tyr, dec in rows:
        line = "%-16s %3s %3s " % (ts, stg, tyr)
        for o in offs:
            if o + 4 <= len(dec):
                val = struct.unpack_from("<f", dec, o)[0]
                line += "%9.4g" % val
            else:
                line += "        -"
        print(line)


if __name__ == "__main__":
    main()
