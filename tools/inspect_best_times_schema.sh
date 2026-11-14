#!/bin/bash
# Read-only: show the live best_times column types and ghost_data_b64 size stats.
# Usage: inspect_best_times_schema.sh
set -e
cd /opt/dr3/web
set -a; . /opt/dr3/web/.env; set +a
/opt/dr3/.venv/bin/python - <<'EOF'
from app import create_app
from app.db import db
from sqlalchemy import text

app = create_app()
with app.app_context():
    print('=== best_times columns (live schema) ===')
    rows = db.session.execute(text(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
        "FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='best_times' "
        "ORDER BY ORDINAL_POSITION")).fetchall()
    for r in rows:
        print('  %-20s %-14s null=%-3s key=%s' % (r[0], r[1], r[2], r[3]))

    print('=== ghost_data_b64 size stats ===')
    row = db.session.execute(text(
        "SELECT COUNT(*) AS n, "
        "       SUM(ghost_data_b64 IS NULL) AS nulls, "
        "       MAX(CHAR_LENGTH(ghost_data_b64)) AS max_chars, "
        "       MAX(LENGTH(ghost_data_b64)) AS max_bytes "
        "FROM best_times")).fetchone()
    print('  rows=%s null_ghost=%s max_chars=%s max_bytes=%s'
          % (row[0], row[1], row[2], row[3]))

    print('=== largest ghosts (top 8) ===')
    for r in db.session.execute(text(
        "SELECT id, username, track, CHAR_LENGTH(ghost_data_b64) AS chars, "
        "       stage_time_ms, submitted_at "
        "FROM best_times WHERE ghost_data_b64 IS NOT NULL "
        "ORDER BY CHAR_LENGTH(ghost_data_b64) DESC LIMIT 8")).fetchall():
        print('  id=%s user=%s track=%s chars=%s time=%sms sub=%s'
              % (r[0], r[1], r[2], r[3], r[4], r[5]))
EOF
