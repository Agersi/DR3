#!/bin/bash
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - <<'EOF'
from app import create_app
app = create_app()
from app.auth import send_email
with app.app_context():
    ok = send_email(
        'nagersoe@hotmail.com',
        'DR3 test: transactional email is live',
        'Hi!\n\nThis is a test from the DR3 (Dirt Rally 3.0) backend at byagersi.com/rally.\n\n'
        'If you received this, Brevo SMTP + domain authentication (noreply@byagersi.com) are working.\n\n'
        'No action needed - you can delete this.',
    )
print("send_email returned:", ok)
EOF
