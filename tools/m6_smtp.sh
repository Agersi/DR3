#!/bin/bash
set -e
ENV=/opt/dr3/web/.env
grep -v -E '^(EMAIL_HOST|EMAIL_PORT|EMAIL_USE_TLS|EMAIL_HOST_USER|EMAIL_HOST_PASSWORD|DEFAULT_FROM_EMAIL)=' "$ENV" > /tmp/env.new
cat >> /tmp/env.new <<'EOF'
EMAIL_HOST=smtp-relay.brevo.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=bf6417001@smtp-brevo.com
EMAIL_HOST_PASSWORD=__FILL_IN__   # SMTP relay password — keep it out of source control
DEFAULT_FROM_EMAIL=noreply@byagersi.com
EOF
cp /tmp/env.new "$ENV"
chmod 600 "$ENV"
chown agersi:agersi "$ENV"
rm -f /tmp/env.new
systemctl restart dr3-web.service
sleep 3
echo "service: $(systemctl is-active dr3-web.service)"
curl -sk -o /dev/null --max-time 15 -w 'rally:%{http_code}\n' https://byagersi.com/rally/
