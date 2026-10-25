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


class Config:
    SECRET_KEY: str = os.environ['SECRET_KEY']
    DATABASE_URL: str = os.environ.get(
        'DATABASE_URL', 'sqlite:///' + os.path.join(os.path.dirname(__file__), '..', 'dev.db')
    )
    SQLALCHEMY_DATABASE_URI: str = DATABASE_URL
    SITE_URL: str = os.environ.get('SITE_URL', 'http://localhost:5001')
    CRON_API_KEY: str = os.environ.get('CRON_API_KEY', '')
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = 'Lax'
    SESSION_COOKIE_SECURE: bool = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
    WTF_CSRF_ENABLED: bool = os.environ.get('DR3_DISABLE_CSRF', '0') != '1'

    SMTP_HOST: str = os.environ.get('EMAIL_HOST', '')
    SMTP_PORT: int = int(os.environ.get('EMAIL_PORT', '587'))
    SMTP_USER: str = os.environ.get('EMAIL_HOST_USER', '')
    SMTP_PASS: str = os.environ.get('EMAIL_HOST_PASSWORD', '')
    SMTP_USE_TLS: bool = os.environ.get('EMAIL_USE_TLS', 'true').lower() == 'true'
    MAIL_FROM: str = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@byagersi.com')
