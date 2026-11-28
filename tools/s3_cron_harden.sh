#!/bin/bash
# S3 (Phase 3, 2026-11-28): cron hardening — live migration.
# - App cron: root-owned script reads CRON_API_KEY from .env, curls gunicorn on localhost.
# - Backup: hardened (pipefail, umask 077, dump sanity check) + .env snapshot +
#   off-site catch-up sync to the always-on PC over Tailscale+SSH. TS_HOST lives in
#   /etc/dr3-backup.conf; empty = sync step skipped gracefully.
# - Locks /opt/dr3/web/.env down to dr3:dr3 600 — agersi loses read access (the old
#   agersi cron was its only consumer). Verified BEFORE the lock, service restarted
#   AFTER to prove a fresh boot reads the new perms.
# Idempotent — safe to re-run. Fresh installs use tools/m6_cron.sh (kept in sync).
set -euo pipefail

# ── 1. App cron tick script ───────────────────────────────────────────────
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

# ── 2. Daily backup script ────────────────────────────────────────────────
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

# ── 3. Backup config (TS_HOST filled in once the PC joins the tailnet) ───
if [ ! -f /etc/dr3-backup.conf ]; then
  printf 'TS_HOST=\n' > /etc/dr3-backup.conf
fi
chown root:root /etc/dr3-backup.conf
chmod 600 /etc/dr3-backup.conf

# ── 4. Cron entries (root runs the scripts; no secrets in crontabs) ───────
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

# ── 5. Verify app cron works BEFORE locking .env down ─────────────────────
/usr/local/bin/dr3-cron.sh && echo APP_CRON_OK

# ── 6. Lock .env down: only the dr3 service user reads it ─────────────────
chown dr3:dr3 /opt/dr3/web/.env
chmod 600 /opt/dr3/web/.env

# ── 7. Post-lockdown checks (restart proves a fresh boot reads new perms) ─
systemctl restart dr3-web.service
sleep 4
echo "service: $(systemctl is-active dr3-web.service)"
ss -ltn | grep 8001 || { echo PORT_8001_NOT_LISTENING; exit 1; }
su -s /bin/sh agersi -c 'grep -m1 -q ^CRON_API_KEY= /opt/dr3/web/.env && echo CRON_ENV_READABLE || echo CRON_ENV_BLOCKED'
ls -la /opt/dr3/web/.env /usr/local/bin/dr3-cron.sh /usr/local/bin/dr3-backup.sh /etc/cron.d/dr3-cron /etc/cron.d/dr3-backup /etc/dr3-backup.conf
