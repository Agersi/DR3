#!/bin/bash
# Sync /tmp/web.env.new -> /opt/dr3/web/.env, then restart dr3-web.
# Verifies every key present in the current .env still exists in the new file
# (no accidental key drops). Optionally pass the expected AUTO_JOIN_CLUB_ID
# value as $1 to also assert that exact line is present.
set -e
for key in $(cut -d= -f1 /opt/dr3/web/.env | grep -v '^#' | grep -v '^[[:space:]]*$'); do
  grep -q "^${key}=" /tmp/web.env.new || { echo "MISSING_KEY: $key"; exit 1; }
done
if [ -n "${1:-}" ]; then
  grep -q "^AUTO_JOIN_CLUB_ID=${1}$" /tmp/web.env.new || { echo "AUTOJOIN_MISMATCH: expected ${1}"; exit 1; }
fi
install -o dr3 -g dr3 -m 600 /tmp/web.env.new /opt/dr3/web/.env
rm -f /tmp/web.env.new
systemctl restart dr3-web.service
sleep 4
echo "active: $(systemctl is-active dr3-web.service)"
ss -ltn | grep 8001 || echo PORT_8001_NOT_LISTENING
ls -la /opt/dr3/web/.env
