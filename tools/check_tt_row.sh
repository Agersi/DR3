#!/bin/bash
# Dump best_times rows for one user + track (all dates, read-only).
# Usage: check_tt_row.sh USERNAME TRACK_ID
set -e
U="${1:?username}"
T="${2:?track_id}"
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT id, username, vehicle_id, track, conditions, category, vclass,
       stage_time_ms,
       IF(splits_json IS NULL, 'NULL', CONCAT('len=', CHAR_LENGTH(splits_json))) AS splits,
       submitted_at
FROM best_times
WHERE username='$U' AND track='$T'
ORDER BY submitted_at;"
