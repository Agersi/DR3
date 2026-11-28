#!/bin/bash
# Verify the additive users stat columns (total_distance_m, stages_driven) exist
# on the production DB after a deploy, and that existing rows carry non-NULL
# defaults. Run: python tools/vps.py --run-script tools/check_users_stat_columns.sh
cd /opt/dr3/web
set -a; . ./.env; set +a
/opt/dr3/.venv/bin/python - <<'PY'
import os
from sqlalchemy import create_engine, inspect, text

eng = create_engine(os.environ["DATABASE_URL"])
cols = {c["name"] for c in inspect(eng).get_columns("users")}
print("total_distance_m present:", "total_distance_m" in cols)
print("stages_driven present:", "stages_driven" in cols)
with eng.connect() as conn:
    n = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
    bad = conn.execute(text(
        "SELECT COUNT(*) FROM users "
        "WHERE total_distance_m IS NULL OR stages_driven IS NULL")).scalar()
print("user rows:", n, "| null-stat rows:", bad)
PY
