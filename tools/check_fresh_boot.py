r"""Fresh-process boot check.

Boots the Flask app in a clean interpreter against an OLD-schema database
(a `users` table missing the W5 token-expiry / PBKDF2-cost columns) and
verifies the additive boot migration adds them — exactly the path the VPS
takes on redeploy, which the smoke suite (fresh schema each run) never
exercises.

Usage:  python tools\check_fresh_boot.py [db_path]
Default db_path: build\bootcheck.db (wiped first; sqlite only).
"""
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / 'dr3' / 'web'

db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'build' / 'bootcheck.db'
if not str(db_path).lower().startswith('sqlite') and db_path.suffix != '.db':
    print(f'refusing non-sqlite path: {db_path}')
    sys.exit(2)
if db_path.exists():
    db_path.unlink()
db_path.parent.mkdir(parents=True, exist_ok=True)

# Old-schema users table: everything the User model had before W5 except
# verify_token_created_at / reset_token_created_at / password_iterations.
OLD_USERS = '''
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(64) NOT NULL UNIQUE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(64) NOT NULL,
    salt VARCHAR(32) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    country VARCHAR(64),
    bio TEXT,
    created_at DATETIME,
    email_verified BOOLEAN,
    verify_token VARCHAR(64),
    reset_token VARCHAR(64),
    steam_account_id BIGINT,
    soft_currency INTEGER DEFAULT 500000,
    hard_currency INTEGER DEFAULT 0,
    garage_slots INTEGER DEFAULT 8,
    garage_json TEXT,
    total_distance_m BIGINT DEFAULT 0,
    stages_driven INTEGER DEFAULT 0,
    favorite_car_id INTEGER,
    favorite_location_id INTEGER,
    show_device_name BOOLEAN,
    show_days_played BOOLEAN,
    show_seconds_played BOOLEAN,
    show_sessions BOOLEAN
)
'''
con = sqlite3.connect(db_path)
con.execute(OLD_USERS)
con.commit()
con.close()

# Env must exist BEFORE `import app` (app.config reads it at import time).
os.environ['SECRET_KEY'] = 'fresh-boot-check'
os.environ['DATABASE_URL'] = f'sqlite:///{db_path}'
sys.path.insert(0, str(WEB))

import app  # noqa: E402

application = app.create_app()
print(f'BOOT OK ({type(application).__name__})')

con = sqlite3.connect(db_path)
cols = {row[1] for row in con.execute('PRAGMA table_info(users)')}
con.close()
missing = [c for c in ('verify_token_created_at', 'reset_token_created_at',
                       'password_iterations') if c not in cols]
if missing:
    print(f'MIGRATION FAILED, missing: {missing}')
    sys.exit(1)
print('MIGRATION OK (all three W5 columns added to old-schema users table)')
