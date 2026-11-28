#!/bin/bash
# S4 (Phase 3, 2026-11-28): least-privilege grants for the dr3 DB accounts.
# No args: print current grants (inspect). With 'apply': revoke ALL on dr3.*
# and grant only what the app needs — SELECT/INSERT/UPDATE/DELETE for data,
# plus CREATE/ALTER/INDEX because create_all() runs every boot and additive
# migrations ALTER tables at startup. DROP/TRIGGER/REFERENCES stay revoked.
set -e
if [ "$1" = "apply" ]; then
    sudo mysql <<'SQL'
REVOKE ALL PRIVILEGES ON dr3.* FROM 'dr3'@'localhost';
REVOKE ALL PRIVILEGES ON dr3.* FROM 'dr3'@'127.0.0.1';
GRANT SELECT, INSERT, UPDATE, DELETE, ALTER, CREATE, INDEX ON dr3.* TO 'dr3'@'localhost';
GRANT SELECT, INSERT, UPDATE, DELETE, ALTER, CREATE, INDEX ON dr3.* TO 'dr3'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
    echo APPLIED
fi
sudo mysql -N -e "SHOW GRANTS FOR 'dr3'@'localhost';"
echo ----
sudo mysql -N -e "SHOW GRANTS FOR 'dr3'@'127.0.0.1';"
