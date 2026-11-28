"""Audit every StageComplete of a test session: request fields (StageTime,
RaceStatus, RecovToService) and the Progress our server / upstream returned
(State, ChampTimeMs), so DNF/wrecked finishes can be compared with clean ones.

Usage:
  python stage_complete_audit.py                    # newest date in captures dir
  python stage_complete_audit.py 20261027           # specific day (YYYYMMDD)
  python stage_complete_audit.py --dir <captures_dir> [date]
"""
import argparse
import json
import os


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="*")
    ap.add_argument("--dir", default=os.path.join(os.environ.get("APPDATA", ""), "DR3", "captures"))
    args = ap.parse_args()

    files = sorted(f for f in os.listdir(args.dir) if f.endswith(".json"))
    dates = set(args.dates) or {f[:8] for f in files}
    rows = []
    for f in files:
        if f[:8] not in dates:
            continue
        d = load(os.path.join(args.dir, f))
        if not d:
            continue
        fn = (d.get("headers") or {}).get("X-EgoNet-Function", "")
        if fn != "RaceNetChallenges.StageComplete":
            continue
        req = d.get("decoded_body") or {}
        resp = d.get("response") or {}
        body = resp.get("body_decoded") or resp.get("body") or {}
        prog = body.get("Progress") if isinstance(body, dict) else None
        ps = ""
        if isinstance(prog, dict):
            ps = f"State={prog.get('State')} t={prog.get('StageTimeMs')} c={prog.get('ChampTimeMs')} al={prog.get('AttemptsLeft')}"
        elif isinstance(prog, list) and prog:
            p0 = prog[0] if isinstance(prog[0], dict) else {}
            ps = f"Prog[{len(prog)}] State={p0.get('State')} c={p0.get('ChampTimeMs')} al={p0.get('AttemptsLeft')}"
        rows.append((
            f[9:15],
            req.get("ChallengeId", req.get("ChallengeID")),
            req.get("StageIndex"),
            req.get("StageTime"),
            req.get("RaceStatus"),
            req.get("RecovToService"),
            "P" if resp.get("proxy") else "L",
            ps,
        ))
    rows.sort()
    for r in rows:
        print(f"{r[0]}  chal={r[1]} S{r[2]} time={r[3]} rs={r[4]} recov={r[5]} [{r[6]}] {r[7]}")
    print(f"TOTAL StageComplete: {len(rows)}")


if __name__ == "__main__":
    main()
