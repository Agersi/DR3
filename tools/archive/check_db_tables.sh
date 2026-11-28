#!/bin/bash
# Post-v0.1.58 rename audit: confirms results->club_results and time_trials
# -> best_times landed, the new best_times key is in place, and stage_runs has
# conditions_id (read-only). Usage: check_db_tables.sh
set -e
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT '== tables ==' AS '';
SHOW TABLES;
SELECT '== best_times key/indexes ==' AS '';
SHOW INDEX FROM best_times;
SELECT '== best_times rows ==' AS '';
SELECT COUNT(*) AS n FROM best_times;
SELECT '== club_results rows ==' AS '';
SELECT COUNT(*) AS n FROM club_results;
SELECT '== stage_runs conditions_id ==' AS '';
SELECT COUNT(*) AS with_conditions FROM stage_runs WHERE conditions_id IS NOT NULL;"
