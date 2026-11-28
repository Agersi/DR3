-- Pattern needs double backslash: MySQL unescapes the string literal first,
-- so 'e2e\\_%' + ESCAPE '\\' is what actually matches e2e_*.
DELETE FROM game_tokens WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e\\_%' ESCAPE '\\');
DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e\\_%' ESCAPE '\\');
DELETE FROM users WHERE username LIKE 'e2e\\_%' ESCAPE '\\';
