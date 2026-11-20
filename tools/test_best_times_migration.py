"""Verify the best_times tyre-compound migration on a simulated old-schema DB.

Builds a scratch SQLite database with the pre-migration best_times schema (no
tyre_compound column, nationality_id present, 4-column unique key) plus
stage_runs rows carrying compounds, boots the web app against it so the startup
migrations run, and asserts:
  - the tyre_compound column was added;
  - zero rows were backfilled from stage_runs (exact-time match preferred, else
    the most recent known-compound run for the same car + track + conditions);
  - uq_best_times_key was re-keyed to the 5 columns;
  - a second app boot is a no-op (idempotent).

Usage: tools/test_best_times_migration.py [db_path]
       (default: <project root>/build/best_migration_test.db, wiped each run)
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WEB = ROOT / 'dr3' / 'web'
sys.path.insert(0, str(WEB))


def main() -> int:
    db_path = (Path(sys.argv[1]) if len(sys.argv) > 1
               else ROOT / 'build' / 'best_migration_test.db')
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    from sqlalchemy import create_engine, text

    engine = create_engine(f'sqlite:///{db_path}')
    with engine.begin() as conn:
        conn.execute(text('''
            CREATE TABLE best_times (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vclass VARCHAR(8) NOT NULL,
                track VARCHAR(8) NOT NULL,
                conditions VARCHAR(8) NOT NULL,
                category INTEGER NOT NULL,
                username VARCHAR(64) NOT NULL,
                stage_time_ms BIGINT NOT NULL,
                vehicle_id INTEGER NOT NULL,
                livery_id INTEGER,
                nationality_id INTEGER,
                using_wheel BOOLEAN,
                using_assists BOOLEAN,
                ghost_data_b64 TEXT,
                splits_json TEXT,
                submitted_at DATETIME
            )'''))
        # Named unique index — exactly what create_all / the v0.1.58 re-key
        # migration produce on every real DB (SQLite autoindexes from an inline
        # UNIQUE constraint are invisible to SQLAlchemy's inspector, so they
        # never occur in practice).
        conn.execute(text(
            'CREATE UNIQUE INDEX uq_best_times_key ON best_times '
            '(vehicle_id, track, conditions, username)'))
        conn.execute(text(
            'CREATE INDEX ix_best_times_username ON best_times (username)'))
    from app.models import StageRun
    StageRun.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO best_times (vclass, track, conditions, category,"
            " username, stage_time_ms, vehicle_id, nationality_id) VALUES"
            " ('97', '636', '5', 1, 'alice', 155990, 100, 31),"
            " ('97', '630', '7', 2, 'bob', 999999, 200, 42),"
            " ('97', '700', '0', 1, 'carol', 424242, 300, 7)"))
        t0 = datetime(2026, 11, 1, 12, 0, 0)
        for r in [
            # alice: exact-time match for her stored best -> compound 2
            dict(username='alice', vehicle_id=100, track_model_id=636,
                 conditions_id=5, time_ms=155990, tyre_compound=2,
                 received_at=t0),
            # bob: no exact match (best is 999999) -> most recent known run
            dict(username='bob', vehicle_id=200, track_model_id=630,
                 conditions_id=7, time_ms=999000, tyre_compound=5,
                 received_at=t0),
            dict(username='bob', vehicle_id=200, track_model_id=630,
                 conditions_id=7, time_ms=1000000, tyre_compound=8,
                 received_at=t0 + timedelta(hours=1)),
        ]:
            conn.execute(StageRun.__table__.insert().values(**r))
    engine.dispose()

    os.environ['SECRET_KEY'] = 'migration-test-key'
    os.environ['DATABASE_URL'] = f'sqlite:///{db_path}'
    os.environ.setdefault('DR3_DISABLE_CSRF', '1')
    from app import create_app
    from app.db import db
    from app.models import TimeTrialEntry

    def best_rows() -> dict:
        with create_app().app_context():
            return {r.username: r for r in db.session.query(TimeTrialEntry).all()}

    first = best_rows()  # boot 1 — the migrations run here

    eng2 = create_engine(f'sqlite:///{db_path}')
    with eng2.connect() as conn:
        key_sql = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE name='uq_best_times_key'"
        )).fetchone()
    eng2.dispose()

    checks = [
        ('column added + backfilled (exact-time match)',
         first['alice'].tyre_compound == 2
         and first['alice'].stage_time_ms == 155990),
        ('backfill falls back to most recent known run',
         first['bob'].tyre_compound == 8),
        ('unresolvable row stays 0',
         first['carol'].tyre_compound == 0),
        ('uq_best_times_key re-keyed to 5 columns',
         key_sql is not None and 'tyre_compound' in key_sql[0]
         and len(key_sql[0].split('(')[1].rstrip(')').split(',')) == 5),
    ]

    # Second boot: everything must be a no-op (idempotent).
    second = best_rows()
    checks.append(('second boot is a no-op (idempotent)',
                   {u: r.stage_time_ms for u, r in first.items()}
                   == {u: r.stage_time_ms for u, r in second.items()}
                   and {u: r.tyre_compound for u, r in first.items()}
                   == {u: r.tyre_compound for u, r in second.items()}))

    failed = 0
    for name, ok in checks:
        print(f'[{"ok" if ok else "FAIL"}] {name}')
        failed += 0 if ok else 1
    print('ALL MIGRATION CHECKS PASSED' if not failed
          else f'{failed} MIGRATION CHECK(S) FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
