"""Verify tuning carry-over across leg transitions from captures: for each
timestamp prefix, collect the TuningSetup blobs seeded in the decodable
Clubs.GetClubs Progress entries AND the blob the game reported in its
StageBegin request, then byte-diff every pair (decompressed payloads).

If a rally->RX transition shows the game's StageBegin blob differing from the
seeded previous-leg blob across parameter regions, the game replaced the
setup on RX entry (see docs/PLAN.md item #17 for the 2026-10-24/26 evidence).

Usage:
  python captures_tuning_carryover.py 20261024-035137 20261024-035956 [more...]
  python captures_tuning_carryover.py ... --dir <captures_dir>
"""
import argparse
import base64
import hashlib
import json
import os
import zlib


def sha(b):
    return hashlib.sha1(b).hexdigest()[:8]


def g(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def dec(raw):
    try:
        return zlib.decompress(raw[16:])
    except Exception as e:
        print("  decompress fail:", e)
        return None


def diff(x, y):
    if x is None or y is None:
        return "n/a"
    n = min(len(x), len(y))
    fd = next((i for i in range(n) if x[i] != y[i]), None)
    nd = sum(1 for i in range(n) if x[i] != y[i]) + abs(len(x) - len(y))
    return f"first_diff={fd} n_diff={nd} (lens {len(x)}/{len(y)})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefixes", nargs="+", help="capture filename prefixes (YYYYMMDD-HHMMSS)")
    ap.add_argument("--dir", default=os.path.join(os.environ.get("APPDATA", ""), "DR3", "captures"))
    args = ap.parse_args()

    collected = []  # (label, decompressed blob)
    for prefix in args.prefixes:
        seeded = {}
        reported = None
        for f in sorted(os.listdir(args.dir)):
            if not f.startswith(prefix) or not f.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(args.dir, f), encoding="utf-8"))
            except Exception:
                continue
            fn = d.get("headers", {}).get("X-EgoNet-Function", "")
            if "GetClubs" in fn:
                body = (d.get("response") or {}).get("body")
                if isinstance(body, dict):
                    for p in body.get("Progress", []):
                        if not isinstance(p, dict):
                            continue
                        t = p.get("TuningSetup") or {}
                        raw = base64.b64decode(t.get("blob_base64", ""))
                        cid = g(p, "ChallengeID", "ChallengeId")
                        print(
                            f"{f[:15]} SEED chal={cid} state={p.get('State')} veh={p.get('VehicleId')} "
                            f"tyres={p.get('TyresRemaining')}/{p.get('TyreCompound')} sha={sha(raw)} len={len(raw)}"
                        )
                        seeded[cid] = raw
            db = d.get("decoded_body") or {}
            if "TuningSetup" in db:
                t = db["TuningSetup"]
                raw = base64.b64decode(t.get("blob_base64", ""))
                print(
                    f"{f[:15]} BEGIN {fn.split('.')[-1]} chal={g(db, 'ChallengeId', 'ChallengeID')} "
                    f"veh={db.get('VehicleId')} stage={db.get('StageIndex')} sha={sha(raw)} len={len(raw)}"
                )
                reported = raw

        for cid, raw in seeded.items():
            pl = dec(raw)
            if pl is not None:
                collected.append((f"{prefix[:15]} seed chal={cid}", pl))
        if reported is not None:
            pl = dec(reported)
            if pl is not None:
                collected.append((f"{prefix[:15]} begin-reported", pl))

    print()
    print("=== blob diffs (decompressed) ===")
    for i in range(len(collected)):
        for j in range(i + 1, len(collected)):
            print(f"{collected[i][0]} vs {collected[j][0]}: {diff(collected[i][1], collected[j][1])}")


if __name__ == "__main__":
    main()
