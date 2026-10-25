from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import smtplib
from email.message import EmailMessage
from functools import wraps
from html import escape as _html_escape

from flask import current_app, g, jsonify, redirect, request, session, url_for

from .db import db
from .models import GameToken, User, generate_game_token, utcnow

log = logging.getLogger(__name__)

PBKDF2_ITERATIONS = 120_000


def hash_password(password: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
    return dk.hex()


def check_password(password: str, user: User) -> bool:
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(user.salt), PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), user.password_hash)


def create_user(username: str, email: str, password: str, display_name: str | None = None,
                country: str = '', bio: str = '', email_verified: bool = False) -> User:
    salt = secrets.token_bytes(16)
    u = User(
        username=username,
        email=email,
        password_hash=hash_password(password, salt.hex()),
        salt=salt.hex(),
        display_name=display_name or username,
        country=country,
        bio=bio,
        email_verified=email_verified,
        verify_token=None if email_verified else secrets.token_urlsafe(32),
    )
    db.session.add(u)
    db.session.commit()
    return u


_URL_RE = re.compile(r'https?://[^\s<>"\']+')


def _text_to_html(body: str) -> str:
    parts = []
    for para in body.split('\n\n'):
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        if not lines:
            continue
        rendered = []
        for ln in lines:
            esc = _html_escape(ln, quote=False)
            if _URL_RE.fullmatch(esc):
                rendered.append(f'<a href="{esc}">click here</a>')
            else:
                rendered.append(_URL_RE.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', esc))
        parts.append('<p>' + '<br>'.join(rendered) + '</p>')
    return '\n'.join(parts)


def send_email(to: str, subject: str, body: str) -> bool:
    cfg = current_app.config
    log.info('Sending email to=%s subject=%r host=%s port=%s',
             to, subject, cfg.get('SMTP_HOST') or '(not set)', cfg.get('SMTP_PORT'))
    if not cfg.get('SMTP_HOST'):
        log.warning('EMAIL_HOST not configured — email not sent (dev mode: check logs)')
        return False
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = cfg['MAIL_FROM']
    msg['To'] = to
    msg.set_content(body)
    msg.add_alternative(_text_to_html(body), subtype='html')
    try:
        if cfg.get('SMTP_USE_TLS', True):
            server = smtplib.SMTP(cfg['SMTP_HOST'], cfg['SMTP_PORT'], timeout=10)
            server.starttls()
        else:
            server = smtplib.SMTP(cfg['SMTP_HOST'], cfg['SMTP_PORT'], timeout=10)
        if cfg.get('SMTP_USER'):
            server.login(cfg['SMTP_USER'], cfg['SMTP_PASS'])
        server.send_message(msg)
        server.quit()
        log.info('Email sent to %s', to)
        return True
    except Exception:
        log.exception('Failed to send email to %s', to)
        return False


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        uid = session.get('user_id')
        if not uid:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'authentication required'}), 401
            return redirect(url_for('pages.login', next=request.path))
        g.user = db.session.get(User, uid)
        if g.user is None:
            session.clear()
            return redirect(url_for('pages.login'))
        return f(*args, **kwargs)
    return wrapper


def verified_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        uid = session.get('user_id')
        if not uid:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'authentication required'}), 401
            return redirect(url_for('pages.login', next=request.path))
        user = db.session.get(User, uid)
        if user is None:
            session.clear()
            return redirect(url_for('pages.login'))
        if not user.email_verified:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'email verification required'}), 403
            from flask import flash
            g.user = user
            flash('Please verify your email first.', 'warning')
            return redirect(url_for('pages.account'))
        g.user = user
        return f(*args, **kwargs)
    return wrapper


def game_auth_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        token = auth[len('Bearer '):] if auth.startswith('Bearer ') else ''
        if not token:
            return jsonify({'ok': False, 'error': 'missing bearer token'}), 401
        gt = GameToken.query.filter_by(token=token).first()
        if gt is None or gt.revoked_at is not None:
            return jsonify({'ok': False, 'error': 'invalid token'}), 401
        g.game_user = gt.user
        return f(*args, **kwargs)
    return wrapper


def cron_auth_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        key = current_app.config.get('CRON_API_KEY', '')
        if not key:
            return jsonify({'ok': False, 'error': 'cron disabled (CRON_API_KEY not set)'}), 503
        supplied = request.headers.get('X-Cron-Key', '')
        if not hmac.compare_digest(supplied, key):
            return jsonify({'ok': False, 'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return wrapper


def issue_game_token(user: User) -> GameToken:
    gt = GameToken(user_id=user.id, token=generate_game_token())
    db.session.add(gt)
    db.session.commit()
    return gt
