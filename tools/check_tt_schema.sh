#!/bin/bash
# Verifies the time_trials table schema on the VPS (columns, unique key, row count),
# in particular that the additive migrations ran at boot:
#   - splits_json column present
#   - unique key is (vehicle_id, track, conditions, category, username)
#   - vehicle_id NOT NULL
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
from app import create_app
from app.db import db

app = create_app()
with app.app_context():
    insp = db.inspect(db.engine)
    cols = {c['name']: c for c in insp.get_columns('time_trials')}
    print('--- columns:')
    for name, c in cols.items():
        print(f"  {name}: {c['type']} nullable={c['nullable']}")
    print('--- indexes:')
    for ix in insp.get_indexes('time_trials'):
        print(f"  {ix['name']}: {list(ix['column_names'])} unique={bool(ix['unique'])}")
    print('--- rows:', db.session.execute(db.text('SELECT COUNT(*) FROM time_trials')).scalar())
EOF
