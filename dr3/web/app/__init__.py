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

    for attempt in range(5):
        try:
            db.create_all()
            return
        except OperationalError as e:
            if '1050' not in str(e) and 'already exists' not in str(e):
                raise
            time.sleep(0.5 * (attempt + 1))


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
