#!/bin/bash
# Dump actual splits_json contents for given stage_stats / best_times ids (read-only).
# Usage: check_splits_values.sh [STATS_IDS] [TT_IDS]   (comma-separated id lists)
set -e
STATS_IDS="${1:-12,14,15,31,32,35}"
TT_IDS="${2:-1,3,4,5}"
chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
export MYSQL_PWD="$PW"
mysql -h 127.0.0.1 -u dr3 dr3 -e "
SELECT '== stage_stats ==' AS '';
SELECT id, username, vehicle_id, track_id, best_time_ms, splits_json
FROM stage_stats WHERE id IN ($STATS_IDS) ORDER BY id;
SELECT '== best_times ==' AS '';
SELECT id, username, vehicle_id, track, stage_time_ms, splits_json
FROM best_times WHERE id IN ($TT_IDS) ORDER BY id;"
