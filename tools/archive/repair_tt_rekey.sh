#!/bin/bash
# One-time repair for the time_trials re-key metadata-lock deadlock (2026-11-01):
# kills the queued MDL waiters left by the buggy migration, then finishes the
# remaining DDL (NOT NULL + unique key) as root with a fail-fast lock timeout.
set -e
echo '--- current waiters:'
sudo mysql -e "SELECT id, time, state, LEFT(info,60) AS info FROM information_schema.processlist WHERE state LIKE '%metadata%' OR info LIKE 'ALTER TABLE time_trials%';"

echo '--- killing MDL waiters on time_trials:'
IDS=$(sudo mysql -N -e "SELECT id FROM information_schema.processlist WHERE state = 'Waiting for table metadata lock';")
for id in $IDS; do sudo mysql -e "KILL $id;" && echo "killed $id"; done
sleep 1

echo '--- current indexes:'
sudo mysql dr3 -e "SHOW INDEX FROM time_trials;"

echo '--- finishing DDL (fail-fast):'
sudo mysql dr3 -e "SET SESSION lock_wait_timeout = 15; ALTER TABLE time_trials MODIFY COLUMN vehicle_id INTEGER NOT NULL;" && echo 'MODIFY OK' || echo 'MODIFY FAILED (still blocked)'

echo '--- final state:'
sudo mysql dr3 -e "SHOW CREATE TABLE time_trials\G"
