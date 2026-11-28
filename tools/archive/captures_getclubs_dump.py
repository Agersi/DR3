"""Dump the raw structure of a decodable Clubs.GetClubs response at a given
timestamp prefix: top-level keys, one full Progress entry, and the Challenges
list. Use this when probing an unknown capture set for the first time.

Usage:
  python captures_getclubs_dump.py 20261024-035137
  python captures_getclubs_dump.py 20261024-035137 --dir <captures_dir>
"""
import argparse
import json
import os


def g(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefix", help="capture filename prefix (YYYYMMDD-HHMMSS)")
    ap.add_argument("--dir", default=os.path.join(os.environ.get("APPDATA", ""), "DR3", "captures"))
    args = ap.parse_args()

    d = None
    for f in sorted(os.listdir(args.dir)):
        if not f.startswith(args.prefix) or not f.endswith(".json"):
            continue
        try:
            cand = json.load(open(os.path.join(args.dir, f), encoding="utf-8"))
        except Exception:
            continue
        if "GetClubs" in cand.get("headers", {}).get("X-EgoNet-Function", ""):
            b = (cand.get("response") or {}).get("body")
            if isinstance(b, dict):
                d = cand
                break
    assert d is not None, f"no decodable GetClubs at {args.prefix}"

    body = d["response"]["body"]
    print("top keys:", list(body.keys()))
    prog = body.get("Progress")
    print("Progress type:", type(prog).__name__)
    if isinstance(prog, list):
        print("num entries:", len(prog))
        for i, p in enumerate(prog[:2]):
            print(f"RAW entry[{i}]:", json.dumps(p, indent=1)[:2000])
    elif isinstance(prog, dict):
        print(json.dumps(prog, indent=1)[:3000])

    ch = body.get("Challenges")
    print("\nChallenges type:", type(ch).__name__)
    if isinstance(ch, list):
        for c in ch:
            if isinstance(c, dict):
                keep = {
                    k: c[k]
                    for k in c
                    if k in ("ChallengeID", "ChallengeId", "Name", "ClubId", "Type", "State", "EventCount")
                }
                print(json.dumps(keep)[:400])
    elif isinstance(ch, dict):
        print(json.dumps(ch, indent=1)[:2000])


if __name__ == "__main__":
    main()
