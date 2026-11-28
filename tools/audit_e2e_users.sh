#!/bin/bash
# Read-only audit: list leftover e2e_* test users (and count their tokens /
# notifications) so m6_cleanup.sql can be run deliberately. The LIKE pattern
# must be 'e2e\\_%' ESCAPE '\\' — see the comment in m6_cleanup.sql. Quoted
# heredocs keep every backslash literal (bash -e double quotes would eat one).
# Usage: audit_e2e_users.sh [users|tokens|notif]  (default: all three)
which_q="${1:-all}"
run_users() {
    sudo mysql -D dr3 <<'SQL'
SELECT id, username FROM users WHERE username LIKE 'e2e\\_%' ESCAPE '\\';
SQL
}
run_tokens() {
    sudo mysql -D dr3 <<'SQL'
SELECT COUNT(*) FROM game_tokens gt JOIN users u ON u.id = gt.user_id WHERE u.username LIKE 'e2e\\_%' ESCAPE '\\';
SQL
}
run_notif() {
    sudo mysql -D dr3 <<'SQL'
SELECT COUNT(*) FROM notifications n JOIN users u ON u.id = n.user_id WHERE u.username LIKE 'e2e\\_%' ESCAPE '\\';
SQL
}
case "$which_q" in
    users) run_users ;;
    tokens) echo "tokens:"; run_tokens ;;
    notif) echo "notif:"; run_notif ;;
    *) run_users; echo ----; run_tokens; echo ----; run_notif ;;
esac
