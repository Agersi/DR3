#!/bin/bash
# Dump per-stage entries (incl. telemetry splits) from results.stages_json for
# one web event (read-only). Shows whether club stage runs stored splits.
# Usage: check_event_results_splits.sh EVENT_ID [USERNAME]
set -e
E="${1:?event id}"
U="${2:-}"
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
if [ -n "$U" ]; then
  mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT r.id AS result, r.username, r.vehicle_id, r.total_time_ms, r.stages_json
FROM results r WHERE r.event_id='$E' AND r.username='$U' ORDER BY r.id;"
else
  mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT r.id AS result, r.username, r.vehicle_id, r.total_time_ms, r.stages_json
FROM results r WHERE r.event_id='$E' ORDER BY r.id;"
fi
