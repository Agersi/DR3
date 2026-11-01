#!/bin/bash
# Dump an event's legs + settings (read-only): per-leg location/class/stages/
# rx_laps and the championship settings JSON.
# Usage: check_event_legs.sh EVENT_ID [EVENT_ID ...]
set -e
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
IDS=""
for e in "$@"; do IDS="$IDS'$e',"; done
IDS=${IDS%,}
mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT id, name, start_time, end_time, settings_json, legs_json
FROM events WHERE id IN ($IDS) ORDER BY start_time;"
