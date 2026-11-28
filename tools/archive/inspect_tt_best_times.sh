#!/bin/bash
# Inspect best_times rows (by id list, default 1113,1114) and the matching
# stage_runs rows for the same user/car/track, to compare how TT splits land
# in each table. Usage: inspect_tt_best_times.sh [id_list]
set -e
IDS="${1:-1113,1114}"
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
MYSQL() { MYSQL_PWD="$PW" mysql -h 127.0.0.1 -u dr3 dr3 -e "$1"; }

echo "== best_times rows =="
MYSQL "SELECT id, username, vehicle_id, track, conditions, tyre_compound, stage_time_ms, submitted_at, LENGTH(splits_json) AS splits_len, LEFT(splits_json, 200) AS splits_head FROM best_times WHERE id IN ($IDS)\\G"

echo "== recent TT/official best_times (last 50 by id) =="
MYSQL "SELECT id, username, vehicle_id, track, conditions, tyre_compound, stage_time_ms, submitted_at, LENGTH(splits_json) AS splits_len FROM best_times ORDER BY id DESC LIMIT 50;"

echo "== stage_runs for the same users/cars/tracks (last 3 days) =="
MYSQL "SELECT r.id, r.username, r.source_type, r.vehicle_id, r.track_model_id, r.challenge_id, r.event_index, r.stage_index, r.time_ms, r.player_finished, r.player_end_status, LENGTH(r.splits_json) AS splits_len, r.received_at FROM stage_runs r JOIN (SELECT DISTINCT username, vehicle_id, track FROM best_times WHERE id IN ($IDS)) k ON k.username=r.username AND k.vehicle_id=r.vehicle_id AND CAST(r.track_model_id AS CHAR)=k.track WHERE r.received_at > NOW() - INTERVAL 3 DAY ORDER BY r.id DESC LIMIT 60;"

echo "== TT stage_runs split coverage (last 14 days) =="
MYSQL "SELECT source_type, COUNT(*) AS runs, SUM(splits_json IS NOT NULL) AS with_splits, SUM(time_ms IS NOT NULL AND time_ms > 0) AS timed FROM stage_runs WHERE received_at > NOW() - INTERVAL 14 DAY GROUP BY source_type;"
