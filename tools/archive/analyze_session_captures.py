"""Group EgoNet capture JSONs by game session (X-EgoNet-SessionID) and print a
per-session timeline of API calls, to reconstruct what the game did around a
crash.  Usage: python analyze_session_captures.py [START] [END]

START/END are HHMM strings (default 0000-2359 = all).  Only captures whose
filename timestamp falls in [START, END] of the latest capture date are shown.
"""
import collections
import glob
import json
import os
import sys


def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else "0000"
    end = sys.argv[2] if len(sys.argv) > 2 else "2359"
    cap_dir = os.path.join(
        os.environ.get("APPDATA", ""), "DR3", "captures")
    files = sorted(glob.glob(os.path.join(cap_dir, "*.json")))
    sess = collections.OrderedDict()
    for f in files:
        base = os.path.basename(f)
        ts = base[:15]  # YYYYMMDD-HHMMSS
        day, hhmm = ts.split("-")
        if not (start <= hhmm <= end):
            continue
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        headers = d.get("headers", {})
        sid = headers.get("X-EgoNet-SessionID", "?")[:8]
        fn = headers.get("X-EgoNet-Function", "?")
        body = d.get("response", {}).get("body") or {}
        req = d.get("decoded_body") or {}
        extra = ""
        if fn == "RaceNetChallenges.StageBegin":
            p = body.get("Progress", {})
            extra = (f"StageIdx={p.get('StageIndex')} chal={req.get('ChallengeId')}"
                     f" compound={req.get('TyreCompound')} rem={req.get('TyresRemaining')}")
        elif fn == "RaceNetChallenges.StageComplete":
            p = body.get("Progress", {})
            extra = (f"reqStage={req.get('StageIndex')} respStage={p.get('StageIndex')}"
                     f" state={p.get('State')} compound={p.get('TyreCompound')}"
                     f" rem={p.get('TyresRemaining')}")
        elif fn == "RaceNetChallenges.GetChallengeRecap":
            extra = str(body)[:260]
        elif fn in ("Clubs.ClubEventStart",):
            extra = str(body)[:260]
        sess.setdefault((day, sid), []).append((ts[11:], fn, extra))
    for (day, sid), calls in sess.items():
        print("#" * 30, "SESSION", day, sid)
        for t, fn, extra in calls:
            if fn in ("Login.Tick", "DataMining.DataEvent"):
                continue
            print(" ", t, fn, extra)


if __name__ == "__main__":
    main()
