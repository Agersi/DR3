#!/bin/bash
# Dump stage_stats personal-best rows (read-only): per car+track best time,
# source label, and whether the stored best carries telemetry splits.
# Usage: check_stage_stats_rows.sh [USERNAME]
set -e
U="${1:-}"
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
if [ -n "$U" ]; then
  mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT id, username, vehicle_id, track_id, best_time_ms, source_type,
       source_event_id, (splits_json IS NULL) AS splits_null, updated_at
FROM stage_stats WHERE username='$U' ORDER BY id;"
else
  mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT id, username, vehicle_id, track_id, best_time_ms, source_type,
       source_event_id, (splits_json IS NULL) AS splits_null, updated_at
FROM stage_stats ORDER BY id;"
fi
