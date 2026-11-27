import os


def _load_dotenv(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            os.environ.setdefault(key.strip(), val.strip())


def split_site_url(site_url: str, script_root: str) -> tuple[str, str]:
    """(base, root) for building absolute URLs. SITE_URL may already end with
    the subpath that also arrives as SCRIPT_NAME (production runs under
    /rally) — strip it so the prefix is never doubled."""
    base = site_url.rstrip('/')
    if script_root and base.endswith(script_root):
        base = base[: -len(script_root)]
    return base, script_root


class Config:
    SECRET_KEY: str = os.environ['SECRET_KEY']
    DATABASE_URL: str = os.environ.get(
        'DATABASE_URL', 'sqlite:///' + os.path.join(os.path.dirname(__file__), '..', 'dev.db')
    )
    SQLALCHEMY_DATABASE_URI: str = DATABASE_URL
    SITE_URL: str = os.environ.get('SITE_URL', 'http://localhost:5001')
    CRON_API_KEY: str = os.environ.get('CRON_API_KEY', '')
    # Club (by id) that every new signup is added to automatically; empty = off.
    AUTO_JOIN_CLUB_ID: str = os.environ.get('AUTO_JOIN_CLUB_ID', '')
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = 'Lax'
    SESSION_COOKIE_SECURE: bool = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
    WTF_CSRF_ENABLED: bool = os.environ.get('DR3_DISABLE_CSRF', '0') != '1'
    # No time-based expiry (Flask-WTF default is 3600s): long-lived forms like
    # the championship builder must not fail with "The CSRF token has expired"
    # when a user leaves the page open. Tokens stay bound to the session, so
    # CSRF protection is unchanged — only the clock limit is removed.
    WTF_CSRF_TIME_LIMIT: int | None = None

    SMTP_HOST: str = os.environ.get('EMAIL_HOST', '')
    SMTP_PORT: int = int(os.environ.get('EMAIL_PORT', '587'))
    SMTP_USER: str = os.environ.get('EMAIL_HOST_USER', '')
    SMTP_PASS: str = os.environ.get('EMAIL_HOST_PASSWORD', '')
    SMTP_USE_TLS: bool = os.environ.get('EMAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_FROM: str = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@byagersi.com')
