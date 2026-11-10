from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, g
from flask_wtf import CSRFProtect

csrf = CSRFProtect()

# External project links (footer + home page).
GITHUB_URL = 'https://github.com/Agersi/DR3'
TWITCH_URL = 'https://www.twitch.tv/agersi'
KO_FI_URL = 'https://ko-fi.com/agersi'
DISCORD_URL = 'https://discord.com/invite/U3h9dWGgGY'


def _parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _timeago(dt: datetime) -> str:
    from .models import utcnow
    diff = utcnow() - dt
    if diff.days > 30:
        return f'{diff.days // 30}mo ago'
    if diff.days > 0:
        return f'{diff.days}d ago'
    h = diff.seconds // 3600
    if h > 0:
        return f'{h}h ago'
    m = diff.seconds // 60
    return f'{m}m ago' if m > 0 else 'just now'


def _countdown(dt: datetime) -> str:
    from .models import utcnow
    diff = dt - utcnow()
    if diff.total_seconds() <= 0:
        return 'Ended'
    d = diff.days
    h = diff.seconds // 3600
    m = (diff.seconds % 3600) // 60
    if d > 0:
        return f'{d}d {h}h'
    if h > 0:
        return f'{h}h {m}m'
    return f'{m}m'


def create_app() -> Flask:
    _load_dotenv_local()
    # Make the sibling dr2server package (protocol data tables) importable.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

    from .config import Config
    from .db import db

    here = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(here / 'templates'),
        static_folder=str(here / 'static'),
    )
    app.config.from_object(Config)
    db.init_app(app)
    csrf.init_app(app)

    @app.template_filter('timeago')
    def timeago_filter(value):
        dt = _parse_dt(value)
        return _timeago(dt) if dt else (value or '')

    @app.template_filter('countdown')
    def countdown_filter(value):
        dt = _parse_dt(value)
        return _countdown(dt) if dt else (value or '')

    @app.context_processor
    def inject_site_links():
        return {'GITHUB_URL': GITHUB_URL, 'TWITCH_URL': TWITCH_URL,
                'KO_FI_URL': KO_FI_URL, 'DISCORD_URL': DISCORD_URL}

    @app.context_processor
    def inject_countries():
        """Country select options + flag helpers for templates (backlog #40).

        ``flag_url(country)`` resolves a self-declared country to its static
        flag image; ``flag_for(username)`` does the same for a driver looked
        up by username (request-cached), for result tables that only carry
        names."""
        from .countries import COUNTRIES, flag_url

        def flag_for(username: str) -> str:
            name = (username or '').strip()
            if not name:
                return ''
            cache = getattr(g, '_flag_cache', None)
            if cache is None:
                cache = g._flag_cache = {}
            if name in cache:
                return cache[name]
            from .models import User
            user = User.query.filter(
                db.func.lower(User.username) == name.lower()
            ).first()
            url = flag_url(user.country) if user is not None else ''
            cache[name] = url
            return url

        return {'countries': COUNTRIES, 'flag_url': flag_url, 'flag_for': flag_for}

    @app.context_processor
    def inject_thumbs():
        """Location + car-class thumbnail resolvers for templates; each
        returns the static URL of the shipped image or ``''`` when none."""
        from .class_thumbs import class_thumb_url
        from .locations import location_thumb_url
        return {'location_thumb_url': location_thumb_url,
                'class_thumb_url': class_thumb_url}

    from .game_api import game_api_bp
    from .pages import register_page_blueprints
    app.register_blueprint(game_api_bp)
    register_page_blueprints(app)

    with app.app_context():
        _create_schema(db)

    _apply_subpath(app)

    return app


def _apply_subpath(app: Flask) -> None:
    """When SITE_URL carries a path (e.g. https://byagersi.com/rally), nginx
    strips that prefix before proxying to gunicorn, so routes stay root-level
    internally; set SCRIPT_NAME per-request so url_for() and redirects emit
    prefixed URLs."""
    from urllib.parse import urlparse

    prefix = urlparse(app.config['SITE_URL']).path.rstrip('/')
    if not prefix:
        return
    inner = app.wsgi_app

    def wsgi_app(environ, start_response):
        environ['SCRIPT_NAME'] = prefix
        return inner(environ, start_response)

    app.wsgi_app = wsgi_app


def _create_schema(db) -> None:
    """create_all, retrying through multi-worker boot races (table already exists)."""
    import time

    from sqlalchemy.exc import OperationalError

    # Renames must precede create_all — otherwise create_all builds empty
    # new-name tables next to the old data.
    _rename_legacy_tables(db)
    for attempt in range(5):
        try:
            db.create_all()
            break
        except OperationalError as e:
            if '1050' not in str(e) and 'already exists' not in str(e):
                raise
            time.sleep(0.5 * (attempt + 1))
    _ensure_draft_permanent_column(db)
    _ensure_user_stat_columns(db)
    _ensure_user_profile_columns(db)
    _ensure_tt_splits_column(db)
    _ensure_tt_vehicle_key(db)
    _ensure_best_times_key(db)
    _ensure_stage_run_columns(db)


def _rename_legacy_tables(db) -> None:
    """One-shot v0.1.58 table renames: results -> club_results (the club-event
    truth), time_trials -> best_times (personal bests from EVERY run type).

    Runs before create_all so the new-name tables are never created empty next
    to the old data. No-ops on fresh databases (neither old name exists) and on
    already-migrated ones (new name present). The RENAME takes a brief exclusive
    metadata lock, so it runs fail-fast like every other boot DDL here."""
    from sqlalchemy import text

    insp = db.inspect(db.engine)
    have = set(insp.get_table_names())
    dialect = db.engine.dialect.name
    for old, new in (('results', 'club_results'), ('time_trials', 'best_times')):
        if old not in have or new in have:
            continue
        try:
            with db.engine.begin() as conn:
                if dialect == 'mysql':
                    conn.execute(text('SET SESSION lock_wait_timeout = 30'))
                    conn.execute(text(f'ALTER TABLE `{old}` RENAME TO `{new}`'))
                else:  # sqlite (only reachable on a stale dev DB)
                    conn.execute(text(f'ALTER TABLE {old} RENAME TO {new}'))
        except Exception as e:
            if '1025' not in str(e) and 'cannot' not in str(e).lower():
                raise


def _ensure_user_stat_columns(db) -> None:
    """Additive migration for databases created before User.total_distance_m /
    stages_driven. create_all only creates missing tables, so pre-existing users
    tables need the columns added explicitly (works on SQLite and MySQL)."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    cols = {c['name'] for c in db.inspect(db.engine).get_columns('users')}
    wanted = {'total_distance_m': 'BIGINT NOT NULL DEFAULT 0',
              'stages_driven': 'INTEGER NOT NULL DEFAULT 0'}
    for name, ddl in wanted.items():
        if name in cols:
            continue
        try:
            with db.engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE users ADD COLUMN {name} {ddl}'))
        except OperationalError as e:
            # Lost the boot race to another worker adding the same column.
            if '1060' not in str(e) and 'duplicate column' not in str(e):
                raise


def _ensure_user_profile_columns(db) -> None:
    """Additive migration for databases created before User favorites + profile
    stat visibility flags. create_all only creates missing tables, so pre-
    existing users tables need the columns added explicitly (works on SQLite and
    MySQL)."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    cols = {c['name'] for c in db.inspect(db.engine).get_columns('users')}
    wanted = {
        'favorite_car_id': 'INTEGER NULL',
        'favorite_location_id': 'INTEGER NULL',
        'show_device_name': 'BOOLEAN NOT NULL DEFAULT 1',
        'show_days_played': 'BOOLEAN NOT NULL DEFAULT 1',
        'show_seconds_played': 'BOOLEAN NOT NULL DEFAULT 1',
        'show_sessions': 'BOOLEAN NOT NULL DEFAULT 1',
    }
    for name, ddl in wanted.items():
        if name in cols:
            continue
        try:
            with db.engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE users ADD COLUMN {name} {ddl}'))
        except OperationalError as e:
            # Lost the boot race to another worker adding the same column.
            if '1060' not in str(e) and 'duplicate column' not in str(e):
                raise


def _ensure_draft_permanent_column(db) -> None:
    """Additive migration for databases created before ChampionshipDraft.permanent.

    create_all only creates missing tables, so pre-existing championship_drafts
    tables need the column added explicitly (works on SQLite and MySQL)."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    cols = {c['name'] for c in db.inspect(db.engine).get_columns('championship_drafts')}
    if 'permanent' in cols:
        return
    try:
        with db.engine.begin() as conn:
            conn.execute(text(
                'ALTER TABLE championship_drafts '
                'ADD COLUMN permanent BOOLEAN NOT NULL DEFAULT 0'))
    except OperationalError as e:
        # Lost the boot race to another worker adding the same column.
        if '1060' not in str(e) and 'duplicate column' not in str(e):
            raise


def _ensure_tt_splits_column(db) -> None:
    """Additive migration for databases created before TimeTrialEntry.splits_json.

    create_all only creates missing tables, so pre-existing best_times
    (renamed from time_trials at boot) need the column added explicitly
    (works on SQLite and MySQL)."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    cols = {c['name'] for c in db.inspect(db.engine).get_columns('best_times')}
    if 'splits_json' in cols:
        return
    try:
        with db.engine.begin() as conn:
            conn.execute(text(
                'ALTER TABLE best_times ADD COLUMN splits_json TEXT NULL'))
    except OperationalError as e:
        # Lost the boot race to another worker adding the same column.
        if '1060' not in str(e) and 'duplicate column' not in str(e):
            raise


def _ensure_tt_vehicle_key(db) -> None:
    """Historical re-key migration (v0.1.53): databases created before
    TimeTrialEntry was keyed per car.

    Old schema: unique (vclass, track, conditions, category, username), vehicle_id
    nullable. New schema: unique (vehicle_id, track, conditions, category,
    username), vehicle_id NOT NULL — vclass is kept as a stored column for future
    class-based UI. No-ops on fresh and already-migrated databases."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    insp = db.inspect(db.engine)
    indexes = insp.get_indexes('best_times')
    old_key = ('vclass', 'track', 'conditions', 'category', 'username')
    if not any(tuple(ix['column_names']) == old_key for ix in indexes):
        return
    name = next(ix['name'] for ix in indexes if tuple(ix['column_names']) == old_key)
    dialect = db.engine.dialect.name
    try:
        with db.engine.begin() as conn:
            if dialect == 'mysql':
                conn.execute(text(
                    f'ALTER TABLE best_times DROP INDEX `{name}`'))
                conn.execute(text(
                    'ALTER TABLE best_times ADD CONSTRAINT uq_time_trials_vehicle '
                    'UNIQUE (vehicle_id, track, conditions, category, username)'))
            else:  # sqlite (only reachable on a stale dev DB)
                conn.execute(text(f'DROP INDEX IF EXISTS {name}'))
                conn.execute(text(
                    'CREATE UNIQUE INDEX uq_time_trials_vehicle ON best_times '
                    '(vehicle_id, track, conditions, category, username)'))
    except OperationalError as e:
        # Lost the boot race to another worker doing the same re-key.
        if not ('1091' in str(e) or '1061' in str(e)
                or 'no such index' in str(e).lower()
                or 'already exists' in str(e).lower()):
            raise

    # vehicle_id NOT NULL — an independent step (a partially migrated DB must be
    # able to finish it even after the old key is gone). The COUNT runs on its
    # own short-lived connection so no transaction is left open while the DDL
    # waits for an exclusive metadata lock (an open session transaction would
    # deadlock the ALTER against itself).
    cols = {c['name']: c for c in db.inspect(db.engine).get_columns('best_times')}
    if dialect == 'mysql' and cols.get('vehicle_id', {}).get('nullable'):
        with db.engine.connect() as conn:
            nulls = conn.execute(
                text('SELECT COUNT(*) FROM best_times WHERE vehicle_id IS NULL')).scalar()
        if nulls == 0:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text('SET SESSION lock_wait_timeout = 30'))
                    conn.execute(text(
                        'ALTER TABLE best_times MODIFY COLUMN vehicle_id INTEGER NOT NULL'))
            except OperationalError:
                pass  # lost the boot race; another worker applied it


def _ensure_best_times_key(db) -> None:
    """v0.1.58 re-key of best_times (renamed from time_trials): the unique key
    drops `category` — personal bests are per user + car + track + conditions
    regardless of run type, and category is kept only as a stored informational
    column (the game's leaderboard-type flag of the best run).

    Steps: drop the old 5-column key, dedupe rows that collide on the new 4-
    column key (keep the fastest; ties prefer the row carrying a ghost), add
    uq_best_times_key, and rename the stale username index. No-ops once the new
    key is in place."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from .models import TimeTrialEntry

    insp = db.inspect(db.engine)
    dialect = db.engine.dialect.name
    indexes = insp.get_indexes('best_times')
    old_key_cols = {'vehicle_id', 'track', 'conditions', 'category', 'username'}
    # A fresh database already has uq_best_times_key from create_all and no old
    # key, so the whole function is a no-op there.
    old_ix = next((ix for ix in indexes
                   if set(ix['column_names']) == old_key_cols), None)

    if old_ix is not None:
        # Dedupe BEFORE the new key lands — on a short-lived session so no
        # transaction outlives the DDL below.
        rows = db.session.query(TimeTrialEntry).all()
        groups = {}
        for r in rows:
            groups.setdefault((r.vehicle_id, r.track, r.conditions, r.username),
                              []).append(r)
        for group in groups.values():
            if len(group) < 2:
                continue
            best_ms = min(r.stage_time_ms or 0 for r in group)
            tied = [r for r in group if (r.stage_time_ms or 0) == best_ms]
            keep = next((r for r in tied if r.ghost_data_b64), tied[0])
            for r in group:
                if r is not keep:
                    db.session.delete(r)
        db.session.commit()

        try:
            with db.engine.begin() as conn:
                if dialect == 'mysql':
                    conn.execute(text('SET SESSION lock_wait_timeout = 30'))
                    conn.execute(
                        text(f'ALTER TABLE best_times DROP INDEX `{old_ix["name"]}`'))
                    conn.execute(text(
                        'ALTER TABLE best_times ADD CONSTRAINT uq_best_times_key '
                        'UNIQUE (vehicle_id, track, conditions, username)'))
                else:  # sqlite (only reachable on a stale dev DB)
                    conn.execute(text(f'DROP INDEX IF EXISTS {old_ix["name"]}'))
                    conn.execute(text(
                        'CREATE UNIQUE INDEX uq_best_times_key ON best_times '
                        '(vehicle_id, track, conditions, username)'))
        except OperationalError as e:
            # Lost the boot race to another worker doing the same re-key.
            if not ('1091' in str(e) or '1061' in str(e)
                    or 'no such index' in str(e).lower()
                    or 'already exists' in str(e).lower()):
                raise

    # Stale username index from the time_trials era — rename on MySQL (SQLite
    # dev DBs are scratch; a fresh one gets the right name from create_all).
    if dialect == 'mysql':
        have_ix = {ix['name'] for ix in db.inspect(db.engine).get_indexes('best_times')}
        if 'ix_time_trials_username' in have_ix and 'ix_best_times_username' not in have_ix:
            try:
                with db.engine.begin() as conn:
                    conn.execute(text(
                        'ALTER TABLE best_times RENAME INDEX ix_time_trials_username '
                        'TO ix_best_times_username'))
            except OperationalError:
                pass  # lost the boot race; another worker renamed it


def _ensure_stage_run_columns(db) -> None:
    """Additive migration for databases created before StageRun.splits_json /
    flat_stage_index / input_method / camera_position + the stats indexes.

    create_all only creates missing tables, so pre-existing stage_runs tables
    need the columns/indexes added explicitly (works on SQLite and MySQL)."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    insp = db.inspect(db.engine)
    cols = {c['name'] for c in insp.get_columns('stage_runs')}
    wanted = {
        'splits_json': 'TEXT NULL',
        'flat_stage_index': 'INTEGER NULL',
        'input_method': 'VARCHAR(16) NULL',
        'camera_position': 'VARCHAR(16) NULL',
        'conditions_id': 'INTEGER NULL',
    }
    for name, ddl in wanted.items():
        if name in cols:
            continue
        try:
            with db.engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE stage_runs ADD COLUMN {name} {ddl}'))
        except OperationalError as e:
            # Lost the boot race to another worker adding the same column.
            if '1060' not in str(e) and 'duplicate column' not in str(e):
                raise

    dialect = db.engine.dialect.name
    wanted_indexes = {
        'ix_stage_runs_track_vehicle': ('track_model_id', 'vehicle_id'),
        'ix_stage_runs_vehicle': ('vehicle_id',),
        'ix_stage_runs_event_slot': ('event_id', 'username', 'flat_stage_index'),
    }
    have = {ix['name']: tuple(ix['column_names']) for ix in insp.get_indexes('stage_runs')}
    for name, columns in wanted_indexes.items():
        if have.get(name) == columns:
            continue
        try:
            with db.engine.begin() as conn:
                if dialect == 'mysql':
                    conn.execute(text(
                        f'ALTER TABLE stage_runs ADD INDEX `{name}` '
                        f'({", ".join(columns)})'))
                else:  # sqlite (only reachable on a stale dev DB)
                    conn.execute(text(
                        f'CREATE INDEX IF NOT EXISTS {name} ON stage_runs '
                        f'({", ".join(columns)})'))
        except OperationalError as e:
            # Lost the boot race to another worker adding the same index.
            if '1061' not in str(e) and 'already exists' not in str(e):
                raise


def _load_dotenv_local() -> None:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in (os.path.join(base, '.env'), os.path.join(base, '..', '.env')):
        if os.path.exists(candidate):
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, val = line.split('=', 1)
                    os.environ.setdefault(key.strip(), val.strip())
            return
