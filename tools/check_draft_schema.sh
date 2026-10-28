#!/bin/bash
# Verifies the championship_drafts table schema on the VPS (column names + types).
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
from app import create_app
from app.db import db

app = create_app()
with app.app_context():
    cols = db.inspect(db.engine).get_columns('championship_drafts')
    for c in cols:
        print(f"{c['name']}: {c['type']}")
EOF
