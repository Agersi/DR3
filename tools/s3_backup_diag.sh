#!/bin/bash
# S3 diagnostic: inspect the no-tables dump + compare with the app's DATABASE_URL.
echo "=== fresh dump head (root, default socket) ==="
mysqldump --single-transaction dr3 2>/tmp/dump_err.txt | head -c 1500
echo ""
echo "=== mysqldump stderr ==="
head -5 /tmp/dump_err.txt
echo "=== DATABASE_URL (password masked) ==="
grep -m1 '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#(://)[^:@/]+@#\1***@#'
echo "=== mysqld sockets + processes ==="
ls -la /var/run/mysqld/ 2>/dev/null || true
ps aux | grep -i [m]ysqld | head -5
