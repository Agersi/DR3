#!/usr/bin/env bash
# Read-only: exercise the deployed /clubs/<id>/event-board API against live data.
# Usage: python tools/vps.py --run-script tools/check_board_api_live.sh [CLUB_ID]
set -u
CLUB="${1:-club-35b1b82e}"
PAGE="http://127.0.0.1:8001/clubs/$CLUB"

curl -s "$PAGE" -o /tmp/board_page.html
EID=$(grep -o '"event_id": *"[^"]*"' /tmp/board_page.html | head -n1 | sed 's/.*"\([^"]*\)"$/\1/')
echo "event_id=$EID"

for STAGE in 0 1; do
  echo "== stage=$STAGE =="
  curl -s "http://127.0.0.1:8001/clubs/$CLUB/event-board?event=$EID&round=0&stage=$STAGE" \
    | python3 -c '
import json, sys
b = json.load(sys.stdin)
info = b.get("info") or {}
rows = b.get("rows") or []
print("keys:", sorted(b.keys()))
print("info.number:", info.get("number"), "car_class:", info.get("car_class"),
      "hardcore_damage:", info.get("hardcore_damage"), "surface_deg:", info.get("surface_deg"))
print("stages_bar:", len(b.get("stages_bar") or []), "rounds:", len(b.get("rounds") or []))
print("rows:", len(rows))
for r in rows[:5]:
    print("  ", r.get("pos"), r.get("username"), "| flag:", bool(r.get("flag_url")),
          "| stage:", r.get("time_text"), "| diff:", repr(r.get("diff_str")),
          "| overall:", r.get("overall_text"), "| dnf:", r.get("dnf"))
'
done
