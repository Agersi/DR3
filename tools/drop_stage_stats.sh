#!/bin/bash
# Drop the retired stage_stats table (v0.1.58 consolidation). Writes were
# retired in v0.1.57 (#56) and its salvageable rows were moved to stage_runs
# (repair_stage_runs_from_stage_stats.sh) — this removes the empty-of-purpose
# remainder. Pre-flight sanity: prints what is being dropped (row count +
# source_type breakdown) alongside the counts of the tables that replace it.
# DDL runs on a short-lived connection with lock_wait_timeout=30 (never hold a
# session transaction open across DDL — see the 2026-11-01 re-key deadlock).
# Idempotent: DROP TABLE IF EXISTS; re-running is a no-op.
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
from sqlalchemy import inspect, text

from app import create_app
from app.db import db
from app.models import StageRun, TimeTrialEntry

app = create_app()
with app.app_context():
    names = inspect(db.engine).get_table_names()
    if 'stage_stats' not in names:
        print('drop_stage_stats: stage_stats already absent - nothing to do')
        raise SystemExit(0)

    with db.engine.connect() as conn:
        total = conn.execute(text('SELECT COUNT(*) FROM stage_stats')).scalar()
        breakdown = conn.execute(text(
            'SELECT source_type, COUNT(*) AS n FROM stage_stats'
            ' GROUP BY source_type ORDER BY n DESC')).fetchall()
        print('drop_stage_stats: dropping %d rows (%s)' % (
            total, ', '.join('%s=%d' % (t, n) for t, n in breakdown)))

    runs = db.session.query(StageRun).count()
    bests = db.session.query(TimeTrialEntry).count()
    print('drop_stage_stats: replacing stores - stage_runs=%d best_times=%d'
          % (runs, bests))

    with db.engine.connect() as conn:
        conn.execute(text('SET SESSION lock_wait_timeout = 30'))
        conn.execute(text('DROP TABLE IF EXISTS stage_stats'))
        conn.commit()

    gone = 'stage_stats' not in inspect(db.engine).get_table_names()
    print('drop_stage_stats: table dropped=%s' % gone)
    if not gone:
        raise SystemExit(1)
EOF
