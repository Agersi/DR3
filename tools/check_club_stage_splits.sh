#!/bin/bash
# Dump a club's results with per-stage split info (read-only). Shows whether
# each stored stage entry carries telemetry splits ("splits len=N") or not.
# Usage: check_club_stage_splits.sh CLUB_NAME [USERNAME]
set -e
C="${1:?club name}"
U="${2:-}"
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
if [ -n "$U" ]; then
  mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT e.id AS event, r.username, r.vehicle_id, r.total_time_ms, r.stages_json
FROM results r
JOIN events e ON e.id = r.event_id
JOIN clubs c ON c.id = e.club_id
WHERE c.name LIKE '%$C%' AND r.username='$U'
ORDER BY e.start_time, r.id;"
else
  mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT e.id AS event, r.username, r.vehicle_id, r.total_time_ms, r.stages_json
FROM results r
JOIN events e ON e.id = r.event_id
JOIN clubs c ON c.id = e.club_id
WHERE c.name LIKE '%$C%'
ORDER BY e.start_time, r.id;"
fi
