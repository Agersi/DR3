#!/bin/bash
# Audit TT record pairing for one user: every timetrial stage_stats row vs the
# matching time_trials row (vehicle+track). Orphans = stats row without a
# time_trials entry (pre-v0.1.45 clients recorded stats only, no submit call).
# Read-only. Usage: check_tt_pairing.sh USERNAME
set -e
U="${1:?username}"
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT s.id AS stats_id, s.vehicle_id, s.track_id, s.best_time_ms,
       IF(s.splits_json IS NULL, 'NULL', CONCAT('len=', CHAR_LENGTH(s.splits_json))) AS splits,
       s.updated_at AS stats_at,
       t.id AS tt_id, t.stage_time_ms AS tt_ms, t.submitted_at AS tt_at,
       CASE WHEN t.id IS NULL THEN 'ORPHAN' ELSE 'paired' END AS status
FROM stage_stats s
LEFT JOIN time_trials t
  ON t.username = s.username AND t.vehicle_id = s.vehicle_id AND t.track = s.track_id
WHERE s.username='$U' AND s.source_type='timetrial'
ORDER BY s.updated_at;"
