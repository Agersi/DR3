#!/bin/bash
# Backfill club membership into the given club as role 'member'.
# Default mode: add every user that is a member of NO club (zero rows in
# club_members). With --all: add every user not yet a member of THIS club
# (users in other clubs are added too; existing members untouched).
# Idempotent — safe to re-run.
# Usage (via tools/vps.py --run-script):
#   python tools\vps.py --run-script tools\backfill_club_members.sh <club_id> [--all] [--dry-run]
set -e
CLUB=${1:-}
shift || true
MODE=""
SCOPE="none"
for a in "$@"; do
  case "$a" in
    --dry-run) MODE="--dry-run" ;;
    --all) SCOPE="all" ;;
    *) echo "unknown flag: $a"; exit 1 ;;
  esac
done
if [ -z "$CLUB" ]; then echo "usage: backfill_club_members.sh <club_id> [--all] [--dry-run]"; exit 1; fi

chmod 600 /opt/dr3/web/.env 2>/dev/null || true
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
MYSQL="mysql -h 127.0.0.1 -u dr3 -p$PW dr3"

$MYSQL -N -e "SELECT name FROM clubs WHERE id='$CLUB';" | { read -r NAME; [ -n "$NAME" ] || { echo "UNKNOWN_CLUB: $CLUB"; exit 1; }; echo "club: $NAME ($CLUB)"; }

if [ "$SCOPE" = "all" ]; then
  WHERE="NOT EXISTS (SELECT 1 FROM club_members m WHERE m.user_id = u.id AND m.club_id='$CLUB')"
  echo '--- users not yet in this club:'
else
  WHERE="NOT EXISTS (SELECT 1 FROM club_members m WHERE m.user_id = u.id)"
  echo '--- users with 0 club memberships:'
fi
$MYSQL -e "
SELECT u.id, u.username, u.email_verified
FROM users u
WHERE $WHERE
ORDER BY u.id;"

if [ "$MODE" = "--dry-run" ]; then echo '--- dry run: no changes made'; exit 0; fi

$MYSQL -e "
INSERT INTO club_members (club_id, user_id, role)
SELECT '$CLUB', u.id, 'member'
FROM users u
WHERE $WHERE;"

echo '--- after backfill:'
$MYSQL -e "
SELECT COUNT(*) AS total_users FROM users;
SELECT COUNT(*) AS members_in_club FROM club_members WHERE club_id='$CLUB';
SELECT COUNT(*) AS not_in_club FROM users u
WHERE NOT EXISTS (SELECT 1 FROM club_members m WHERE m.user_id = u.id AND m.club_id='$CLUB');"
