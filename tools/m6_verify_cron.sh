#!/bin/bash
KEY=$(grep -m1 ^CRON_API_KEY= /opt/dr3/web/.env | cut -d= -f2)
curl -s -o /dev/null --max-time 60 -w 'cron-as-agersi:%{http_code}\n' -X POST -H "X-Cron-Key: $KEY" https://byagersi.com/rally/api/cron
mysqldump --single-transaction dr3 | gzip > /var/backups/dr3/test-manual.sql.gz
ls -la /var/backups/dr3/
zcat /var/backups/dr3/test-manual.sql.gz | grep -c 'CREATE TABLE' || true
rm -f /var/backups/dr3/test-manual.sql.gz
