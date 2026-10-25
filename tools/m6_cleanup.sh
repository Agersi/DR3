#!/bin/bash
set -e
mysql dr3 <<'SQL'
DELETE FROM game_tokens WHERE user_id IN (SELECT id FROM users WHERE username REGEXP '^e2e_');
DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE username REGEXP '^e2e_');
DELETE FROM users WHERE username REGEXP '^e2e_';
SQL
echo "cleanup done"
mysql dr3 -N -e "SELECT 'users_left', COUNT(*) FROM users WHERE username REGEXP '^e2e_';"
mysql dr3 -N -e "SELECT 'tokens_total', COUNT(*) FROM game_tokens;"
