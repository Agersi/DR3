"""One-off: timeline of all StageBegin captures (time, vehicle, stage, compound,
tyres_remaining, tuning-blob byte 17, blob sha1) to spot per-stage patterns."""
import base64
import glob
import hashlib
import json
import os
import zlib

CAPDIR = r"C:\Users\Agersi\AppData\Roaming\DR3\captures"


def main():
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

            def g(k):
                if k in db:
                    return db[k]
                for v in db.values():
                    if isinstance(v, dict) and k in v:
                        return v[k]
                return None

            ts = os.path.basename(f)[:15]
            tun = g("TuningSetup") or {}
            raw = base64.b64decode(tun.get("blob_base64", "")) if tun.get("blob_base64") else b""
            dec = zlib.decompress(raw[16:]) if len(raw) > 16 and raw[16:18] == b"\x78\x9c" else b""
            b17 = dec[17] if len(dec) > 17 else -1
            rows.append((ts, g("VehicleId"), g("StageIndex"), g("TyreCompound"),
                         g("TyresRemaining"), b17, hashlib.sha1(dec).hexdigest()[:8],
                         dec[10:18].hex() if len(dec) >= 18 else "", dec))
    rows.sort()
    print("%-16s %5s %3s %3s %3s %4s  %s  %s" % ("time", "veh", "stg", "cmp", "tyr", "b17", "blob", "bytes10-17"))
    prev = None
    for r in rows:
        print("%-16s %5s %3s %3s %3s %4d  %s  %s" % (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7]))
        if prev is not None and prev[1] == r[1] and prev[2] + 1 == r[2]:
            a, b = prev[8], r[8]
            n = min(len(a), len(b))
            idx = [k for k in range(n) if a[k] != b[k]]
            print("    diff vs prev stage: offsets %s" % idx)
        prev = r


if __name__ == "__main__":
    main()
