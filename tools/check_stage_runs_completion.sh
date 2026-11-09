#!/bin/bash
# stage_runs completion audit (read-only): total runs, clean finishes
# (player_finished = 1) and non-finished rows. Explains the difference
# between the /rally/drivers "Stages driven" total and the home page's
# "Stages completed" number. Usage: check_stage_runs_completion.sh [USERNAME]
set -e
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
if [ -n "${1:-}" ]; then
  mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT username, COUNT(*) AS total_runs,
       SUM(player_finished=1) AS completed,
       SUM(player_finished=0) AS not_completed
FROM stage_runs WHERE username='$1' GROUP BY username;"
else
  mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT COUNT(*) AS total_runs,
       SUM(player_finished=1) AS completed,
       SUM(player_finished=0) AS not_completed FROM stage_runs;
SELECT '== per-user ==' AS '';
SELECT username, COUNT(*) AS total_runs,
       SUM(player_finished=1) AS completed,
       SUM(player_finished=0) AS not_completed
FROM stage_runs GROUP BY username ORDER BY total_runs DESC;"
fi
