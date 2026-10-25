DELETE FROM game_tokens WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e\_%' ESCAPE '_');
DELETE FROM notifications WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e\_%' ESCAPE '_');
DELETE FROM users WHERE username LIKE 'e2e\_%' ESCAPE '_';
