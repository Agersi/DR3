"""Verify spare-tyre extraction from TuningSetup blobs in local captures.

Scans capture JSON files for StageBegin requests (decoded_body with
ChallengeId + StageIndex + TuningSetup), decodes the tuning blob with
dr3/dr2server/tuning.py and prints the extracted spare count per stage.

Usage:
    python tools/check_spare_tyres_captures.py [CAPTURES_DIR] [CHALLENGE_ID]

Defaults: CAPTURES_DIR = %APPDATA%\\DR3\\captures, CHALLENGE_ID = all.
"""
import base64
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dr3"))
from dr2server.tuning import TuningBlob  # noqa: E402


def extract_blob(ts):
    if isinstance(ts, dict):
        b64 = ts.get("blob_base64") or ts.get("base64") or ""
    elif isinstance(ts, str):
        b64 = ts
    else:
        return None
    if not b64:
        return None
    try:
        return base64.b64decode(b64)
    except Exception:
        return None


def main():
    cap_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.environ.get("APPDATA", ""), "DR3", "captures")
    want_chal = int(sys.argv[2]) if len(sys.argv) > 2 else None

    rows = []
    for path in sorted(glob.glob(os.path.join(cap_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            continue
        db = d.get("decoded_body") or {}
        if not ("ChallengeId" in db and "StageIndex" in db and "TuningSetup" in db):
            continue
        chal = db.get("ChallengeId")
        if want_chal is not None and chal != want_chal:
            continue
        raw = extract_blob(db.get("TuningSetup"))
        blob = TuningBlob.decode(raw) if raw else None
        spares = blob.spare_tyres() if blob else None
        size = len(blob.payload) if blob else None
        ts = os.path.basename(path)[:15]
        rows.append((ts, chal, db.get("VehicleId"), db.get("StageIndex"),
                     size, spares))

    print("captures dir:", cap_dir)
    print("%-17s %-9s %-6s %-6s %-6s %s" %
          ("capture", "chal", "veh", "stage", "psize", "spares"))
    for ts, chal, veh, stage, size, spares in rows:
        print("%-17s %-9s %-6s %-6s %-6s %s" %
              (ts, chal, veh, stage, size, spares if spares is not None else "?"))


if __name__ == "__main__":
    main()
