#!/bin/bash
# Backfill club membership: add every user that is a member of NO club
# (zero rows in club_members) to the given club as role 'member'.
# Idempotent — users already in any club are never touched.
# Usage (via tools/vps.py --run-script):
#   python tools\vps.py --run-script tools\backfill_club_members.sh <club_id> [--dry-run]
set -e
CLUB=${1:-}
MODE=${2:-}
if [ -z "$CLUB" ]; then echo "usage: backfill_club_members.sh <club_id> [--dry-run]"; exit 1; fi

chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
MYSQL="mysql -h 127.0.0.1 -u dr3 -p$PW dr3"

$MYSQL -N -e "SELECT name FROM clubs WHERE id='$CLUB';" | { read -r NAME; [ -n "$NAME" ] || { echo "UNKNOWN_CLUB: $CLUB"; exit 1; }; echo "club: $NAME ($CLUB)"; }

echo '--- users with 0 club memberships:'
$MYSQL -e "
SELECT u.id, u.username, u.email_verified
FROM users u
WHERE NOT EXISTS (SELECT 1 FROM club_members m WHERE m.user_id = u.id)
ORDER BY u.id;"

if [ "$MODE" = '--dry-run' ]; then echo '--- dry run: no changes made'; exit 0; fi

$MYSQL -e "
INSERT INTO club_members (club_id, user_id, role)
SELECT '$CLUB', u.id, 'member'
FROM users u
WHERE NOT EXISTS (SELECT 1 FROM club_members m WHERE m.user_id = u.id);"

echo '--- after backfill:'
$MYSQL -e "
SELECT COUNT(*) AS total_users FROM users;
SELECT COUNT(*) AS members_in_club FROM club_members WHERE club_id='$CLUB';
SELECT COUNT(*) AS still_memberless FROM users u
WHERE NOT EXISTS (SELECT 1 FROM club_members m WHERE m.user_id = u.id);"
