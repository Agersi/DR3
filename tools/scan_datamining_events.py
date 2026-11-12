"""Scan DR3 EgoNet captures for DataMining.DataEvent payloads.

Summarises which game event names appear and what fields they carry, so the
streaming-overlay state can be extended with data that is available for
official dailies / time trials as well as club races.

Usage:
    python tools/scan_datamining_events.py [CAPTURES_DIR]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
from typing import Any, Dict


DEFAULT_CAPTURES = pathlib.Path.home() / "AppData" / "Roaming" / "DR3" / "captures"

# Fields the overlay writer could potentially use, shown in samples.
INTERESTING_KEYS = {
    "event",
    "challenge_id",
    "event_index",
    "stage_index",
    "challenge_type",
    "track_id",
    "track_model_id",
    "location_id",
    "vehicle_class_id",
    "vehicle_id",
    "vehicle_livery_id",
    "driver_name",
    "player_finished",
    "race_position",
    "journey_event_id",
    "journey_stage_id",
    "g_country_code",
    "damage_type",
    "stage_time_seconds",
}


def _data_of(record: Dict[str, Any]) -> Dict[str, Any]:
    body = record.get("decoded_body") or record.get("body") or {}
    if not isinstance(body, dict):
        return {}
    stats = body.get("Statistics")
    if not isinstance(stats, dict):
        return {}
    data = stats.get("Data")
    if not isinstance(data, dict):
        return {}

    # DataMining payloads are columnar: {event, keys: [...], values: [...]}.
    # Zip them into a flat field map when possible.
    keys = data.get("keys")
    values = data.get("values")
    if isinstance(keys, list) and isinstance(values, list):
        flat = dict(zip(keys, values))
        flat["event"] = data.get("event", "<unknown>")
        return flat
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("captures_dir", nargs="?", default=DEFAULT_CAPTURES)
    args = ap.parse_args()

    root = pathlib.Path(args.captures_dir)
    if not root.is_dir():
        raise SystemExit(f"captures dir not found: {root}")

    counts: collections.Counter = collections.Counter()
    keys_by_event: Dict[str, set] = {}
    samples: Dict[str, Dict[str, Any]] = {}

    for path in sorted(root.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(record, dict):
            continue
        headers = record.get("headers") or {}
        if headers.get("X-EgoNet-Function") != "DataMining.DataEvent":
            continue
        data = _data_of(record)
        event = str(data.get("event", "<unknown>"))
        counts[event] += 1
        keys_by_event.setdefault(event, set()).update(data.keys())
        if event not in samples:
            samples[event] = {k: data[k] for k in INTERESTING_KEYS if k in data}

    print(f"Scanned {root}")
    print("\nEVENTS:")
    for event, n in counts.most_common():
        print(f"{n}\t{event}")

    print("\nINTERESTING KEYS PER EVENT:")
    for event in sorted(counts):
        keys = sorted(keys_by_event.get(event, set()))
        print(f"\n{event}:")
        print(", ".join(keys) or "(none)")

    print("\nSAMPLES (interesting fields only):")
    for event in sorted(samples):
        print(f"\n{event}:")
        print(json.dumps(samples[event], indent=2, default=str))


if __name__ == "__main__":
    main()
