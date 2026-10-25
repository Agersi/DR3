#!/bin/bash
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - <<'EOF'
import smtplib
from app import create_app
create_app()  # loads /opt/dr3/web/.env into env, same path gunicorn uses
from app.config import Config as C
print("host:", C.SMTP_HOST, "port:", C.SMTP_PORT, "tls:", C.SMTP_USE_TLS)
print("user:", C.SMTP_USER, "from:", C.MAIL_FROM)
s = smtplib.SMTP(C.SMTP_HOST, C.SMTP_PORT, timeout=15)
if C.SMTP_USE_TLS:
    s.starttls()
s.login(C.SMTP_USER, C.SMTP_PASS)
print("SMTP_AUTH_OK")
s.quit()
EOF
