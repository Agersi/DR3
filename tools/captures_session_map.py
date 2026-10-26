"""Map a session's Clubs.GetClubs responses: which challenge/club/events each
decodable capture carries (v2 multi-event serving = one leg per call, so the
ChallengeID changes per leg).

Usage:
  python captures_session_map.py                    # every decodable GetClubs in the captures dir
  python captures_session_map.py 20261024-035137 [more prefixes...]
  python captures_session_map.py --dir <captures_dir> ...
"""
import argparse
import json
import os


def decodable_getclubs(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None
    if "GetClubs" not in d.get("headers", {}).get("X-EgoNet-Function", ""):
        return None
    body = (d.get("response") or {}).get("body")
    return body if isinstance(body, dict) else None


def g(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prefixes", nargs="*", help="capture filename prefixes (YYYYMMDD-HHMMSS)")
    ap.add_argument("--dir", default=os.path.join(os.environ.get("APPDATA", ""), "DR3", "captures"))
    args = ap.parse_args()

    for f in sorted(os.listdir(args.dir)):
        if not f.endswith(".json"):
            continue
        if args.prefixes and not any(f.startswith(p) for p in args.prefixes):
            continue
        body = decodable_getclubs(os.path.join(args.dir, f))
        if body is None:
            continue
        print(f"=== {f[:15]} ===")
        for c in body.get("Challenges", []):
            evs = []
            for e in c.get("Events", []):
                st = (e.get("Stages") or [{}])[0]
                evs.append(
                    "E%d(Disc%d,t%d)" % (e.get("EventId"), e.get("DisciplineId"), st.get("TrackModelId"))
                )
            print(
                "  chal=%s club=%s name=%r events=[%s]"
                % (g(c, "ChallengeID", "ChallengeId"), g(c, "ClubId"), c.get("Name"), ", ".join(evs))
            )


if __name__ == "__main__":
    main()
