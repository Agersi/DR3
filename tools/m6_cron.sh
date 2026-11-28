#!/bin/bash
# S3 (Phase 3, 2026-11-28): cron hardening — fresh-install path.
# Root-owned job scripts read CRON_API_KEY from .env themselves; no secrets in
# crontabs; .env locked to dr3:dr3 600. Live migration = tools/s3_cron_harden.sh
# (keep the embedded script bodies in sync between the two files).
set -e
mkdir -p /var/backups/dr3

cat > /usr/local/bin/dr3-cron.sh <<'EOF'
#!/bin/bash
# DR3 app cron tick (every 5 min). Reads CRON_API_KEY from .env, hits gunicorn on localhost.
set -euo pipefail
KEY=$(grep -m1 ^CRON_API_KEY= /opt/dr3/web/.env | cut -d= -f2)
if [ -z "$KEY" ]; then echo "dr3-cron: CRON_API_KEY missing from /opt/dr3/web/.env"; exit 1; fi
curl -sf --max-time 60 -X POST -H "X-Cron-Key: $KEY" http://127.0.0.1:8001/api/cron > /dev/null \
  || { echo "dr3-cron: tick failed (non-2xx or unreachable)"; exit 1; }
EOF
chown root:root /usr/local/bin/dr3-cron.sh
chmod 700 /usr/local/bin/dr3-cron.sh

cat > /usr/local/bin/dr3-backup.sh <<'EOF'
#!/bin/bash
# DR3 daily backup: mysqldump + .env snapshot, then off-site catch-up sync.
# State file /var/backups/dr3/.last_offsite = last date fully synced to the PC;
# each run pushes every local file NEWER than it (missed days catch up).
set -euo pipefail
umask 077

DIR=/var/backups/dr3
CONF=/etc/dr3-backup.conf
STATE="$DIR/.last_offsite"
KEY=/root/.ssh/dr3_backup
DAY=$(date +%F)
mkdir -p "$DIR"

# local dump (sanity-checked) + .env snapshot
OUT="$DIR/$DAY.sql.gz"
mysqldump --single-transaction dr3 | gzip > "$OUT"
if [ ! -s "$OUT" ]; then echo "dr3-backup: empty dump"; rm -f "$OUT"; exit 1; fi
# grep -c (not -q): -q exits on first match -> SIGPIPE to zcat -> pipefail false-positive
if ! zcat "$OUT" | grep -c 'CREATE TABLE' > /dev/null; then echo "dr3-backup: dump has no tables"; rm -f "$OUT"; exit 1; fi
cp /opt/dr3/web/.env "$DIR/$DAY.env"

# off-site sync (skipped until TS_HOST is set in $CONF and the key exists)
TS_HOST=""
if [ -f "$CONF" ]; then TS_HOST=$(grep -m1 '^TS_HOST=' "$CONF" | cut -d= -f2); fi
if [ -n "$TS_HOST" ] && [ -f "$KEY" ]; then
  LAST=$(cat "$STATE" 2>/dev/null || echo "")
  SCP="scp -q -i $KEY -o BatchMode=yes -o ConnectTimeout=30"
  for f in "$DIR"/*.sql.gz; do
    if [ ! -e "$f" ]; then continue; fi
    d=$(basename "$f" .sql.gz)
    if [ -z "$LAST" ] || [[ "$d" > "$LAST" ]]; then
      # absolute remote path: dr3backup has no registered Windows profile, so relative
      # paths would resolve against C:\Windows instead of its home dir
      $SCP "$f" "dr3backup@$TS_HOST:C:/Users/dr3backup/backups/dr3/$d.sql.gz"
      if [ -f "$DIR/$d.env" ]; then
        $SCP "$DIR/$d.env" "dr3backup@$TS_HOST:C:/Users/dr3backup/backups/dr3/$d.env"
      fi
    fi
  done
  date +%F > "$STATE"
  echo "dr3-backup: off-site sync complete (marker -> $(cat "$STATE"))"
else
  echo "dr3-backup: off-site sync skipped (TS_HOST unset or key missing)"
fi

# local retention: 7 days
find "$DIR" -name '*.sql.gz' -mtime +7 -delete
find "$DIR" -name '*.env' -mtime +7 -delete
echo "dr3-backup: done ($DAY)"
EOF
chown root:root /usr/local/bin/dr3-backup.sh
chmod 700 /usr/local/bin/dr3-backup.sh

if [ ! -f /etc/dr3-backup.conf ]; then
  printf 'TS_HOST=\n' > /etc/dr3-backup.conf
fi
chown root:root /etc/dr3-backup.conf
chmod 600 /etc/dr3-backup.conf

cat > /etc/cron.d/dr3-cron <<'CRON'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root /usr/local/bin/dr3-cron.sh
CRON
chmod 644 /etc/cron.d/dr3-cron
cat > /etc/cron.d/dr3-backup <<'BACKUP'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
15 3 * * * root /usr/local/bin/dr3-backup.sh
BACKUP
chmod 644 /etc/cron.d/dr3-backup

# .env is dr3-only (S3): cron runs as root and reads CRON_API_KEY itself.
chown dr3:dr3 /opt/dr3/web/.env
chmod 600 /opt/dr3/web/.env

systemctl is-active cron
ls -la /etc/cron.d/ | grep dr3
/usr/local/bin/dr3-cron.sh && echo APP_CRON_OK
