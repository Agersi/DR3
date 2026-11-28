"""Chronological trace of tuning state in a session's captures: every
StageBegin/StageComplete request with its TuningSetup blob (sha1[:8] + raw
length + tyres/compound) plus the seeded Progress entries from each decodable
Clubs.GetClubs response. Use it to see what the server seeded vs what the
game reported per leg/stage.

Usage:
  python captures_tuning_trace.py [YYYYMMDD date prefix, default: all] [--dir <captures_dir>]
"""
import argparse
import base64
import hashlib
import json
import os


def sha(b):
    return hashlib.sha1(b).hexdigest()[:8]


def tuning_info(t):
    if not isinstance(t, dict):
        return None
    raw = base64.b64decode(t.get("blob_base64", ""))
    return {"sha": sha(raw), "len": len(raw)}


def g(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", default="", help="capture date prefix (YYYYMMDD)")
    ap.add_argument("--dir", default=os.path.join(os.environ.get("APPDATA", ""), "DR3", "captures"))
    args = ap.parse_args()

    files = sorted(
        f for f in os.listdir(args.dir) if f.endswith(".json") and f.startswith(args.date)
    )

    print("=== STAGE EVENTS (StageBegin/StageComplete requests) ===")
    for f in files:
        try:
            d = json.load(open(os.path.join(args.dir, f), encoding="utf-8"))
        except Exception:
            continue
        fn = d.get("headers", {}).get("X-EgoNet-Function", "")
        if "StageBegin" not in fn and "StageComplete" not in fn:
            continue
        db = d.get("decoded_body") or {}
        ti = tuning_info(db.get("TuningSetup"))
        print(
            f[:15],
            fn.split(".")[-1],
            "chal=", g(db, "ChallengeId", "ChallengeID"),
            "evt=", db.get("EventIndex"),
            "stage=", db.get("StageIndex"),
            "veh=", db.get("VehicleId"),
            "tyres=", db.get("TyresRemaining"), "/", db.get("TyreCompound"),
            "tuning=", ti,
        )

    print()
    print("=== GETCLUBS SEEDED PROGRESS ENTRIES ===")
    for f in files:
        try:
            d = json.load(open(os.path.join(args.dir, f), encoding="utf-8"))
        except Exception:
            continue
        fn = d.get("headers", {}).get("X-EgoNet-Function", "")
        if "GetClubs" not in fn:
            continue
        body = (d.get("response") or {}).get("body")
        if not isinstance(body, dict):
            continue
        prog = body.get("Progress")
        entries = []
        if isinstance(prog, list):
            entries = [p for p in prog if isinstance(p, dict)]
        elif isinstance(prog, dict):
            for k, v in prog.items():
                if isinstance(v, list):
                    entries.extend(p for p in v if isinstance(p, dict))
        if not entries:
            continue
        print(f[:15])
        for p in entries:
            print(
                "   ",
                "chal=", g(p, "ChallengeID", "ChallengeId"),
                "state=", p.get("State"),
                "veh=", p.get("VehicleId"),
                "tyres=", p.get("TyresRemaining"), "/", p.get("TyreCompound"),
                "tuning=", tuning_info(p.get("TuningSetup")),
            )


if __name__ == "__main__":
    main()
