#!/bin/bash
set -e
# .env is dr3-only (S3, 2026-11-28): cron runs as root and reads CRON_API_KEY itself.
chown dr3:dr3 /opt/dr3/web/.env
chmod 600 /opt/dr3/web/.env
PW=$(grep '^DATABASE_URL=' /opt/dr3/web/.env | sed -E 's#.*//dr3:([^@]+)@.*#\1#')
if [ -z "$PW" ]; then echo "PARSE_FAIL"; exit 1; fi
sudo mysql <<SQL
CREATE DATABASE IF NOT EXISTS dr3 CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'dr3'@'localhost' IDENTIFIED BY '$PW';
CREATE USER IF NOT EXISTS 'dr3'@'127.0.0.1' IDENTIFIED BY '$PW';
# Least-privilege set (S4): CREATE/INDEX/ALTER needed for boot create_all + additive migrations.
GRANT SELECT, INSERT, UPDATE, DELETE, ALTER, CREATE, INDEX ON dr3.* TO 'dr3'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE, ALTER, CREATE, INDEX ON dr3.* TO 'dr3'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
MYSQL_PWD="$PW" mysql -h 127.0.0.1 -u dr3 -e "SELECT 1;" >/dev/null && echo DB_USER_OK
sudo mysql -N -e "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA ORDER BY 1;"
