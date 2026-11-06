#!/bin/bash
# Dump the most recent stage_stats + best_times rows (read-only).
# Usage: check_recent_stats.sh [LIMIT]   (default 40)
set -e
LIMIT="${1:-40}"
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT '== stage_stats ==' AS '';
SELECT id, username, vehicle_id, track_id, best_time_ms,
       IF(splits_json IS NULL, 'NULL', CONCAT('len=', CHAR_LENGTH(splits_json))) AS splits,
       source_type, IFNULL(source_event_id, '-') AS src_evt, updated_at
FROM stage_stats ORDER BY updated_at DESC LIMIT $LIMIT;
SELECT '== best_times ==' AS '';
SELECT id, username, vehicle_id, track, conditions, category, vclass, stage_time_ms,
       IF(splits_json IS NULL, 'NULL', CONCAT('len=', CHAR_LENGTH(splits_json))) AS splits,
       submitted_at
FROM best_times ORDER BY submitted_at DESC LIMIT $LIMIT;"
