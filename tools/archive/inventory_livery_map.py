"""Extract the VehicleId -> LiveryId map from the upstream GetInventory
template and compare it against dispatcher._LIVERY_FOR_VEHICLE.

Usage:
  python tools/inventory_livery_map.py            # diff + full inventory map
  python tools/inventory_livery_map.py --emit     # print a Python dict literal
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.egonet import decode_stream  # noqa: E402
from dr2server.dispatcher import _LIVERY_FOR_VEHICLE  # noqa: E402


def plain(o):
    if isinstance(o, dict):
        return {k: plain(v) for k, v in o.items()}
    if isinstance(o, list):
        return [plain(v) for v in o]
    if hasattr(o, "value"):
        return o.value
    return o


def main():
    data = (ROOT / "dr3" / "data" / "upstream_templates"
            / "RaceNetInventory_GetInventory.bin").read_bytes()
    top = decode_stream(data)
    inv = plain(top["Inventory"])
    pairs = {}
    for v in inv.get("Vehicles", []):
        pairs[int(v["VehicleId"])] = int(v["LiveryId"])

    print(f"inventory vehicles: {len(pairs)}, dispatcher table: "
          f"{len(_LIVERY_FOR_VEHICLE)}")
    mismatch = {k: (cur, pairs.get(k)) for k, cur in _LIVERY_FOR_VEHICLE.items()
                if pairs.get(k) != cur}
    print("current-table entries not matching inventory:", mismatch or "none")
    missing = {k: v for k, v in sorted(pairs.items())
               if k not in _LIVERY_FOR_VEHICLE}
    print(f"inventory vehicles missing from dispatcher table: {len(missing)}")

    if "--emit" in sys.argv:
        items = ", ".join(f"{k}: {v}" for k, v in sorted(pairs.items()))
        print("{\n    " + items + ",\n}")
    else:
        print(json.dumps({str(k): v for k, v in sorted(pairs.items())}))


if __name__ == "__main__":
    main()
