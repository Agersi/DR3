"""Analyze a test session's request captures: RPC counts, club-flow timeline
(StartChallenge / StageBegin / StageComplete / GetChallengeRecap / leaderboards)
and leaderboard response field dumps.

Usage:
  python analyze_session.py                          # newest date in the captures dir
  python analyze_session.py 20261027                 # specific day (YYYYMMDD)
  python analyze_session.py --dir <captures_dir> [date]
  python analyze_session.py --lb-only                # just leaderboard field dumps
  python analyze_session.py 20261027 --timeline 04:25-04:36   # every RPC in a window
"""
import argparse
import json
import os

CLUB_FLOW = (
    "RaceNetChallenges.StartChallenge",
    "RaceNetChallenges.StageBegin",
    "RaceNetChallenges.StageComplete",
    "RaceNetChallenges.GetChallengeRecap",
    "Clubs.GetChampionshipLeaderboard",
    "Clubs.GetChampionshipFriendsLeaderboard",
)

LEADERBOARDS = (
    "Clubs.GetChampionshipLeaderboard",
    "Clubs.GetChampionshipFriendsLeaderboard",
    "RaceNetLeaderboard.GetLeaderboardEntries",
    "RaceNetLeaderboard.GetFriendsEntries",
    "TimeTrial.GetLeaderboardId",
)


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def resp_body(d):
    r = d.get("response") or {}
    for key in ("body_decoded", "body"):
        v = r.get(key)
        if isinstance(v, dict):
            return v
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="*", help="YYYYMMDD prefixes to include")
    ap.add_argument("--dir", default=os.path.join(os.environ.get("APPDATA", ""), "DR3", "captures"))
    ap.add_argument("--lb-only", action="store_true")
    ap.add_argument("--timeline", metavar="HH:MM-HH:MM",
                    help="print EVERY rpc in the given time window (local/proxy + key fields)")
    args = ap.parse_args()

    tlo = thi = None
    if args.timeline:
        a, b = args.timeline.split("-", 1)
        tlo, thi = a.strip(), b.strip()

    files = sorted(f for f in os.listdir(args.dir) if f.endswith(".json"))
    if not files:
        print("no captures found in", args.dir)
        return
    dates = set(args.dates) or {f[:8] for f in files}
    files = [f for f in files if f[:8] in dates]

    counts = {}
    timeline = []
    lb_dumps = 0
    for f in files:
        d = load(os.path.join(args.dir, f))
        if not d:
            continue
        fn = (d.get("headers") or {}).get("X-EgoNet-Function", "?")
        counts[fn] = counts.get(fn, 0) + 1
        ts = f[:15].replace("-", " ")
        if fn in CLUB_FLOW:
            req = d.get("decoded_body") or {}
            line = f"{ts}  {fn}"
            for k in ("ChallengeId", "ChallengeID", "ClubId", "StageIndex", "EventIndex"):
                if k in req:
                    line += f" {k}={req[k]}"
            if fn.endswith("StageComplete"):
                for k in ("StageTime", "MetersDriven", "RaceStatus", "ChampRank", "EventRank", "RecovToService"):
                    if k in req:
                        line += f" {k}={req[k]}"
            body = resp_body(d) or {}
            prog = body.get("Progress") or {}
            if prog:
                line += (f" -> State={prog.get('State')} StageTimeMs={prog.get('StageTimeMs')} "
                         f"ChampTimeMs={prog.get('ChampTimeMs')} AttemptsLeft={prog.get('AttemptsLeft')}")
            recap = body.get("Recap")
            if recap is not None:
                line += f" -> Recap[{len(recap)}]: " + ", ".join(
                    f"S{r.get('StageIndex')}:st={r.get('State')},t={r.get('StageTimeMs')},c={r.get('CumulTimeMs')}"
                    for r in recap)
            if fn.startswith("Clubs.GetChampionship"):
                entries = body.get("Entries") or []
                line += f" -> TotalEntries={body.get('TotalEntries')} PlayerRank={body.get('PlayerRank')}"
                if entries:
                    line += f" first={json.dumps(entries[0])[:260]}"
            timeline.append(line)
        elif fn in LEADERBOARDS and lb_dumps < 3:
            body = resp_body(d) or {}
            entries = body.get("Entries") or []
            if entries:
                lb_dumps += 1
                print(f"=== {f[:15]} {fn} (req {json.dumps(d.get('decoded_body') or {})[:200]}) ===")
                print(json.dumps(entries[0], indent=1)[:1500])
                if len(entries) > 1:
                    print("--- entry 2 ---")
                    print(json.dumps(entries[1], indent=1)[:800])

    if tlo is not None:
        def _t(s):
            return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"
        tl = []
        for f in files:
            d = load(os.path.join(args.dir, f))
            if not d:
                continue
            t = _t(f[9:15])
            if not (tlo <= t <= thi):
                continue
            fn = (d.get("headers") or {}).get("X-EgoNet-Function", "?")
            resp = d.get("response") or {}
            mark = "P" if isinstance(resp, dict) and resp.get("proxy") else "L"
            req = d.get("decoded_body") or {}
            body = resp_body(d) or {}
            bits = []
            for k in ("ChallengeID", "ChallengeId", "ClubId", "StageIndex", "EventIndex",
                      "State", "StageTimeMs", "CumulTimeMs", "AttemptsLeft"):
                if k in req:
                    bits.append(f"{k}={req[k]}")
            prog = body.get("Progress") or {}
            if isinstance(prog, list) and prog:
                p0 = prog[0] if isinstance(prog[0], dict) else {}
                bits.append(f"->Prog[{len(prog)}]State={p0.get('State')},"
                            f"c={p0.get('ChampTimeMs')},al={p0.get('AttemptsLeft')}")
            elif isinstance(prog, dict) and prog:
                bits.append(f"->State={prog.get('State')},t={prog.get('StageTimeMs')},"
                            f"c={prog.get('ChampTimeMs')},al={prog.get('AttemptsLeft')}")
            recap = body.get("Recap")
            if recap is not None:
                bits.append(f"->Recap[{len(recap)}]: " + ", ".join(
                    f"S{r.get('StageIndex')}:st={r.get('State')},t={r.get('StageTimeMs')},"
                    f"c={r.get('CumulTimeMs')}" for r in recap if isinstance(r, dict)))
            bt = d.get("body_text") or ""
            if fn == "DataMining.DataEvent":
                import re as _re
                m = _re.search(r"dstr.{0,8}([A-Za-z][A-Za-z_]{3,})\s*$", bt)
                bits.append(f"DATAEVENT={m.group(1) if m else '?'}")
            tl.append((t, mark, fn, " ".join(bits)))
        print(f"\n== full timeline {tlo}-{thi} ({len(tl)} rpcs) ==")
        for t, mark, fn, bits in tl:
            print(f"{t} [{mark}] {fn}{'  ' + bits if bits else ''}")

    if not args.lb_only:
        print("== RPC counts ==")
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"{v:5d}  {k}")
        print("\n== club flow timeline ==")
        for line in timeline:
            print(line)


if __name__ == "__main__":
    main()
