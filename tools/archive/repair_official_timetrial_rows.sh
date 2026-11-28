#!/bin/bash
# Re-stamp spurious 'timetrial' stage_stats rows created by the pre-v0.1.56
# official-PostTime bug (#49-official): the game fires TimeTrial.PostTime after
# every completed OFFICIAL stage and the old client recorded it as a time-trial
# personal best. Affected row ids are identified from captures (official
# StageBegin/StageComplete + PostTime payloads with matching car/track/time);
# the rows keep their times, only source_type becomes 'official'.
# Usage: repair_official_timetrial_rows.sh ROW_ID [ROW_ID ...]
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
IDS="$*"
/opt/dr3/.venv/bin/python - "$IDS" <<'EOF'
import sys

from app import create_app
from app.db import db
from app.models import StageStat

ids = [int(a) for a in sys.argv[1].split() if a]
app = create_app()
with app.app_context():
    rows = db.session.query(StageStat).filter(StageStat.id.in_(ids)).all()
    changed = []
    for r in rows:
        if r.source_type == 'timetrial':
            r.source_type = 'official'
            changed.append(r.id)
    db.session.commit()
    print('repair_official_timetrial_rows: restamped=%s (of ids %s)'
          % (changed or '-', ids))
EOF
