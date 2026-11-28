"""Scan EgoNet capture JSONs for tyre-state signals.

Usage: python scan_tyre_state_captures.py [START] [END] [CAPTURE_DIR]

START/END are HHMM strings (default 0000-2359). Prints StageBegin requests,
StageComplete responses, and DataMining StageEnded snapshots with their tyre
fields, sorted by capture timestamp.
"""
import glob
import json
import os
import sys


def _val(value):
    return getattr(value, "value", value)


def _progress(body):
    if not isinstance(body, dict):
        return {}
    progress = body.get("Progress")
    if isinstance(progress, dict):
        return progress
    if isinstance(progress, list) and progress:
        return progress[0] if isinstance(progress[0], dict) else {}
    return {}


def _datamining_fields(payload):
    data = payload.get("Statistics", {}).get("Data", {})
    if data.get("event") != "StageEnded":
        return None
    keys = data.get("keys")
    values = data.get("values")
    if not isinstance(keys, list) or not isinstance(values, list):
        return None
    fields = dict(zip(keys, values))
    wanted = (
        "challenge_id", "event_index", "stage_index", "vehicle_id",
        "player_finished", "player_end_status", "tyre_compound_id",
        "tyre_wear_state",
    )
    return {k: _val(fields.get(k)) for k in wanted}


def main() -> None:
    start = sys.argv[1] if len(sys.argv) > 1 else "0000"
    end = sys.argv[2] if len(sys.argv) > 2 else "2359"
    cap_dir = (sys.argv[3] if len(sys.argv) > 3 else
               os.path.join(os.environ.get("APPDATA", ""), "DR3", "captures"))
    rows = []
    for path in glob.glob(os.path.join(cap_dir, "*.json")):
        base = os.path.basename(path)
        stamp = base[:16]
        day = stamp[:8]
        hhmm = stamp[9:13]
        if not (start <= hhmm <= end):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:
            continue
        headers = d.get("headers", {})
        fn = headers.get("X-EgoNet-Function", "?")
        sid = str(headers.get("X-EgoNet-SessionID", "?"))[:8]
        req = d.get("decoded_body") or {}
        body = (d.get("response") or {}).get("body") or {}
        if fn == "RaceNetChallenges.StageBegin":
            rows.append((stamp, sid, "BEGIN",
                         f"chal={_val(req.get('ChallengeId'))} "
                         f"ev={_val(req.get('EventIndex'))} "
                         f"st={_val(req.get('StageIndex'))} "
                         f"vid={_val(req.get('VehicleId'))} "
                         f"compound={_val(req.get('TyreCompound'))} "
                         f"rem={_val(req.get('TyresRemaining'))}"))
        elif fn == "RaceNetChallenges.StageComplete":
            prog = _progress(body)
            rows.append((stamp, sid, "COMPLETE",
                         f"chal={_val(req.get('ChallengeId'))} "
                         f"ev={_val(req.get('EventIndex'))} "
                         f"st={_val(req.get('StageIndex'))} "
                         f"resp_st={_val(prog.get('StageIndex'))} "
                         f"state={_val(prog.get('State'))} "
                         f"compound={_val(prog.get('TyreCompound'))} "
                         f"rem={_val(prog.get('TyresRemaining'))}"))
        elif fn == "DataMining.DataEvent":
            fields = _datamining_fields(req)
            if fields:
                rows.append((stamp, sid, "STAGE_ENDED",
                             f"chal={fields.get('challenge_id')} "
                             f"ev={fields.get('event_index')} "
                             f"st={fields.get('stage_index')} "
                             f"vid={fields.get('vehicle_id')} "
                             f"finished={fields.get('player_finished')} "
                             f"end={fields.get('player_end_status')} "
                             f"compound={fields.get('tyre_compound_id')} "
                             f"wear={fields.get('tyre_wear_state')}"))
    for stamp, sid, kind, extra in sorted(rows):
        print(stamp, sid, kind, extra)


if __name__ == "__main__":
    main()
