#!/bin/bash
set -e
ENV=/opt/dr3/web/.env
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
grep -v -E '^(EMAIL_HOST|EMAIL_PORT|EMAIL_USE_TLS|EMAIL_HOST_USER|EMAIL_HOST_PASSWORD|DEFAULT_FROM_EMAIL)=' "$ENV" > "$TMP"
# The .env loader does NOT strip inline comments — never append a trailing
# comment to a value line (it becomes part of the value).
cat >> "$TMP" <<'EOF'
EMAIL_HOST=smtp-relay.brevo.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=bf6417001@smtp-brevo.com
EMAIL_HOST_PASSWORD=__FILL_IN__
DEFAULT_FROM_EMAIL=noreply@byagersi.com
EOF
cp "$TMP" "$ENV"
# .env is dr3-only (S3, 2026-11-28): cron runs as root and reads CRON_API_KEY itself.
chown dr3:dr3 "$ENV"
chmod 600 "$ENV"
systemctl restart dr3-web.service
sleep 3
echo "service: $(systemctl is-active dr3-web.service)"
curl -sk -o /dev/null --max-time 15 -w 'rally:%{http_code}\n' https://byagersi.com/rally/
