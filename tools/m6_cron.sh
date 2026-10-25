#!/bin/bash
set -e
mkdir -p /var/backups/dr3
cat > /etc/cron.d/dr3-cron <<'CRON'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * agersi KEY=$(grep -m1 ^CRON_API_KEY= /opt/dr3/web/.env | cut -d= -f2); curl -s -o /dev/null --max-time 60 -X POST -H "X-Cron-Key: $KEY" https://byagersi.com/rally/api/cron
CRON
chmod 644 /etc/cron.d/dr3-cron
cat > /etc/cron.d/dr3-backup <<'BACKUP'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
15 3 * * * root mysqldump --single-transaction dr3 | gzip > /var/backups/dr3/$(date +\%F).sql.gz; find /var/backups/dr3 -name '*.sql.gz' -mtime +7 -delete
BACKUP
chmod 644 /etc/cron.d/dr3-backup
systemctl is-active cron
ls -la /etc/cron.d/ | grep dr3
