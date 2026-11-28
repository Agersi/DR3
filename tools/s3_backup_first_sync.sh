#!/bin/bash
# S3: point the off-site sync at the tailnet PC + first manual catch-up run.
set -euo pipefail

printf 'TS_HOST=serverpc-agersi\n' > /etc/dr3-backup.conf
chown root:root /etc/dr3-backup.conf
chmod 600 /etc/dr3-backup.conf
echo "conf: $(cat /etc/dr3-backup.conf)"

/usr/local/bin/dr3-backup.sh

echo "=== local /var/backups/dr3 ==="
ls -la /var/backups/dr3

echo "=== remote backups/dr3 (via new key; remote shell is cmd) ==="
ssh -i /root/.ssh/dr3_backup -o BatchMode=yes -o ConnectTimeout=20 dr3backup@serverpc-agersi 'dir C:\Users\dr3backup\backups\dr3'
