#!/bin/bash
# Splits coverage over time: which stage_stats / time_trials rows have splits,
# grouped by day (read-only). Shows when split capture started working per source.
set -e
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT '== stage_stats by day ==' AS '';
SELECT DATE(updated_at) AS day, source_type,
       COUNT(*) AS rows_n,
       SUM(splits_json IS NOT NULL) AS with_splits,
       GROUP_CONCAT(DISTINCT track_id ORDER BY track_id SEPARATOR ',') AS tracks
FROM stage_stats GROUP BY DATE(updated_at), source_type ORDER BY day;
SELECT '== time_trials by day ==' AS '';
SELECT DATE(submitted_at) AS day,
       COUNT(*) AS rows_n,
       SUM(splits_json IS NOT NULL) AS with_splits,
       GROUP_CONCAT(DISTINCT track ORDER BY track SEPARATOR ',') AS tracks
FROM time_trials GROUP BY DATE(submitted_at) ORDER BY day;
SELECT '== stage_stats rows WITH splits (all) ==' AS '';
SELECT id, username, vehicle_id, track_id, best_time_ms, source_type,
       IFNULL(source_event_id, '-') AS src_evt, updated_at
FROM stage_stats WHERE splits_json IS NOT NULL ORDER BY updated_at;"
