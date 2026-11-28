#!/usr/bin/env python3
"""Reconstruct the EgoNet request sequence from capture files and flag
mid-event rejoins.

Capture filenames start with a ``YYYYMMDD-HHMMSS`` timestamp (older files end
``-<seq>.json``, newer ones ``_<appVersion>_<captureType>.json``); each file
holds one request (method, path, decoded_body) plus optionally the response.
NOTE: every capture's ``path`` is the generic proxy endpoint
(``/RP17/1.18.0/STEAM/``), so message type is classified from the
``decoded_body`` key set instead:

  StageBegin   : ChallengeId + EventIndex + StageIndex + TuningSetup
  StageComplete: ChallengeId + CompDamage + StageTime
  GetClubs     : ClubLimit / InclCrossPlat

A GetClubs that arrives BETWEEN two stage requests of the same challenge is
flagged as a mid-event rejoin (player exited and rejoined). For each flagged
rejoin the tool prints the VehicleDamage relayed in the GetClubs response
Progress entries next to the CompDamage the game later reported at the end of
the next stage, so "fresh start vs carried state" is visible at a glance.

Usage:
    python check_capture_rejoin_sessions.py [CAPTURE_DIR] [--window MINUTES]
"""
import datetime as dt
import json
import os
import re
import sys

CAP_DEFAULT = r"C:\Users\Agersi\AppData\Roaming\DR3\captures"


def val(x):
    """EgoNet values may be plain or {'value': ...} dicts."""
    if isinstance(x, dict):
        return x.get("value")
    return x


def classify(body):
    keys = set(body.keys())
    if {"ChallengeId", "StageIndex", "TuningSetup"} <= keys:
        return "begin"
    if {"ChallengeId", "CompDamage", "StageTime"} <= keys:
        return "complete"
    if keys & {"ClubLimit", "InclCrossPlat"}:
        return "getclubs"
    if {"Category", "ConditionsId", "GhostData"} <= keys:
        return "posttime"
    return None


def unpack4(v):
    v = val(v)
    if not isinstance(v, int):
        return None
    return [(v >> (8 * i)) & 0xFF for i in range(4)]


def damage_line(vd, prefix=""):
    if not isinstance(vd, dict):
        return ""
    ww = unpack4(vd.get("WheelsWear"))
    wi = unpack4(vd.get("WheelsImpact"))
    parts = []
    if ww is not None:
        parts.append(f"wear[{','.join(str(b) for b in ww)}]")
    if wi is not None:
        parts.append(f"impact[{','.join(str(b) for b in wi)}]")
    return prefix + " ".join(parts)


def progress_of(resp):
    """Yield (state, target_stage, tyres_remaining, vehicle_damage dict)."""
    if not isinstance(resp, dict):
        return
    rbody = resp.get("body_decoded")
    if not isinstance(rbody, dict):
        return
    prog = rbody.get("Progress")
    if isinstance(prog, dict):
        prog = [prog]
    for pe in prog or []:
        if not isinstance(pe, dict):
            continue
        yield (val(pe.get("State")), val(pe.get("TargetStageIndex")),
               val(pe.get("TyresRemaining")), pe.get("VehicleDamage"))


def main():
    args = list(sys.argv[1:])
    cap_dir = CAP_DEFAULT
    window = 45 * 60
    if "--window" in args:
        i = args.index("--window")
        window = int(args[i + 1]) * 60
        del args[i:i + 2]
    if args and not args[0].startswith("-"):
        cap_dir = args[0]

    files = sorted(f for f in os.listdir(cap_dir) if f.endswith(".json"))
    parsed = []
    n_resp = 0
    for fn in files:
        m = re.match(r"(\d{8})-(\d{6})", fn)
        if not m:
            continue
        try:
            with open(os.path.join(cap_dir, fn), encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        body = data.get("decoded_body") or {}
        kind = classify(body) if isinstance(body, dict) else None
        if kind is None:
            continue
        d = dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        resp = data.get("response")
        if isinstance(resp, dict) and isinstance(resp.get("body_decoded"), dict):
            n_resp += 1
        parsed.append((d, fn, kind, body, resp))
    parsed.sort(key=lambda x: x[0])

    sessions = []
    cur = []
    prev_t = None
    for d, fn, kind, body, resp in parsed:
        if prev_t is not None and (d - prev_t).total_seconds() > window:
            if cur:
                sessions.append(cur)
            cur = []
        cur.append((d, fn, kind, body, resp))
        prev_t = d
    if cur:
        sessions.append(cur)

    print(f"{len(files)} files, {len(parsed)} classified "
          f"({n_resp} with decoded response), {len(sessions)} sessions "
          f"(gap > {window // 60} min)")

    rejoin_count = 0
    for si, sess in enumerate(sessions):
        print(f"\n=== session {si}: {sess[0][0]:%m-%d %H:%M} .. "
              f"{sess[-1][0]:%m-%d %H:%M} ({len(sess)} requests) ===")
        # GetClubs carries no ChallengeId, so associate by time: a GetClubs
        # within 15 min of the previous stage request is a mid-event rejoin
        # for that challenge.
        last_stage_t = None   # (dt, chal, kind, stage)
        pending_rejoin = set()  # challenges whose next stage end is post-rejoin
        for d, fn, kind, body, resp in sess:
            chal = val(body.get("ChallengeId"))
            stg = val(body.get("StageIndex"))
            line = f"  {d:%H:%M:%S} {kind}"
            if kind == "begin":
                line += (f" chal={chal} stage={stg} "
                         f"tyres={val(body.get('TyresRemaining'))} "
                         f"compound={val(body.get('TyreCompound'))} "
                         f"veh={val(body.get('VehicleId'))}/"
                         f"{val(body.get('VehicleInstId'))}")
                for state, tgt, tr, vd in progress_of(resp):
                    line += (f"\n      <-resp: state={state} target={tgt} "
                             f"tyres={tr} {damage_line(vd)}")
                last_stage_t = (d, chal, "begin", stg)
            elif kind == "complete":
                cd = body.get("CompDamage") or {}
                line += (f" chal={chal} stage={stg} "
                         f"race={val(body.get('RaceStatus'))} "
                         + damage_line(cd))
                last_stage_t = (d, chal, "complete", stg)
                if chal in pending_rejoin:
                    rejoin_count += 1
                    print(line + "\n      ^^^ first stage end after REJOIN — "
                                  "compare with relayed damage above")
                    pending_rejoin.discard(chal)
            elif kind == "getclubs":
                flag = ""
                if last_stage_t is not None and \
                        (d - last_stage_t[0]).total_seconds() < 15 * 60:
                    lt = last_stage_t
                    flag = (f"  <<< REJOIN (after {lt[2]} "
                            f"chal={lt[1]} stage={lt[3]} "
                            f"{(d - lt[0]).total_seconds():.0f}s later)")
                    pending_rejoin.add(lt[1])
                line += flag
                for state, tgt, tr, vd in progress_of(resp):
                    line += (f"\n      relayed: state={state} target={tgt} "
                             f"tyres={tr} {damage_line(vd)}")
            print(line)

    print(f"\ntotal mid-event rejoins detected: {rejoin_count}")


if __name__ == "__main__":
    main()
