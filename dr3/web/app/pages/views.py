"""Web pages (session auth). Functional first pass — visual redesign at M4."""
from __future__ import annotations

import json
import re
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from ..auth import (
    check_password,
    create_user,
    login_required,
    send_email,
    verified_required,
)
from ..db import db
from ..events import event_is_active, event_is_upcoming, get_all_events, sort_events
from ..models import (
    Club,
    ClubMember,
    Event,
    GameToken,
    Notification,
    Result,
    User,
    utcnow,
)

pages_bp = Blueprint('pages', __name__)

_USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,32}$')


def _verify_link(user: User) -> str:
    base = current_app.config['SITE_URL'].rstrip('/')
    return f"{base}/verify_email/{user.verify_token}"


def _reset_link(user: User) -> str:
    base = current_app.config['SITE_URL'].rstrip('/')
    return f"{base}/reset_password/{user.reset_token}"


def _home_stats(now) -> dict:
    """Site-wide stats for the front page (backlog #29): driver/club counts,
    total completed stages, and the most recent stage times players posted."""
    from ..results_view import format_ms
    from ..scoring import car_label

    club_name = {c.id: c.name for c in Club.query.all()}
    display_name = {u.username: u.display_name for u in User.query.all()}
    events = Event.query.all()
    event_of = {e.id: e for e in events}

    active_clubs = set()
    for e in events:
        if not e.club_id:
            continue
        ed = e.to_dict()
        if event_is_active(ed, now) or event_is_upcoming(ed, now):
            active_clubs.add(e.club_id)

    stages_done = 0
    recent = []
    for r in Result.query.all():
        best_at, best_time = None, 0
        for s in json.loads(r.stages_json or '[]'):
            t = int(s.get('time_ms', 0) or 0)
            if t <= 0:
                continue
            stages_done += 1
            sa = s.get('submitted_at')
            if sa and (best_at is None or sa > best_at):
                best_at, best_time = sa, t
        if not best_at:
            continue
        ev = event_of.get(r.event_id)
        recent.append({
            'driver': display_name.get(r.username, r.username),
            'club': club_name.get(ev.club_id, '') if ev and ev.club_id else '',
            'car': car_label({'vehicle_id': r.vehicle_id, 'car': r.car}),
            'time_text': format_ms(best_time),
            'at': best_at,
        })
    recent.sort(key=lambda x: x['at'], reverse=True)

    return {
        'drivers': User.query.count(),
        'clubs': Club.query.count(),
        'active_clubs': len(active_clubs),
        'stages_done': stages_done,
        'recent': recent[:8],
    }


@pages_bp.route('/')
def home():
    return render_template('home.html', stats=_home_stats(utcnow()))


# ── Auth ─────────────────────────────────────────────────

@pages_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = User.query.filter(
            db.func.lower(User.username) == username.lower()
        ).first()
        if user is None or not check_password(password, user):
            flash('Invalid username or password.', 'error')
            return redirect(url_for('pages.login'))
        session['user_id'] = user.id
        return redirect(request.args.get('next') or url_for('pages.dashboard'))
    return render_template('login.html')


@pages_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm') or ''
        display_name = (request.form.get('display_name') or '').strip() or username
        country = (request.form.get('country') or '').strip()

        errors = []
        if not _USERNAME_RE.match(username):
            errors.append('Username must be 3-32 characters: letters, numbers, underscore.')
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            errors.append('Enter a valid email address.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if password != confirm:
            errors.append('Passwords do not match.')
        if User.query.filter(db.func.lower(User.username) == username.lower()).first():
            errors.append('That username is taken.')
        if User.query.filter_by(email=email).first():
            errors.append('That email is already registered.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('register.html', username=username, email=email)

        user = create_user(username, email, password, display_name=display_name, country=country)
        send_email(
            user.email,
            'Verify your Dirt Rally 3.0 account',
            f"Hi {user.display_name},\n\n"
            f"Confirm your email to activate your account:\n\n{_verify_link(user)}\n",
        )
        flash('Account created. Check your inbox to verify your email.', 'info')
        return redirect(url_for('pages.login'))
    return render_template('register.html')


@pages_bp.route('/verify_email/<token>')
def verify_email(token: str):
    user = User.query.filter_by(verify_token=token).first()
    if user is None or user.email_verified:
        flash('That verification link is invalid or already used.', 'error')
        return redirect(url_for('pages.login'))
    user.email_verified = True
    user.verify_token = None
    db.session.commit()
    session['user_id'] = user.id
    flash('Email verified — welcome!', 'success')
    return redirect(url_for('pages.dashboard'))


@pages_bp.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        user = User.query.filter_by(email=email).first()
        if user is not None:
            import secrets
            user.reset_token = secrets.token_urlsafe(32)
            db.session.commit()
            send_email(
                user.email,
                'Reset your Dirt Rally 3.0 password',
                f"Reset your password here:\n\n{_reset_link(user)}\n",
            )
        flash('If that address is registered, a reset link has been sent.', 'info')
        return redirect(url_for('pages.login'))
    return render_template('forgot_password.html')


@pages_bp.route('/reset_password/<token>', methods=['GET', 'POST'])
def reset_password(token: str):
    user = User.query.filter_by(reset_token=token).first()
    if user is None:
        flash('That reset link is invalid.', 'error')
        return redirect(url_for('pages.login'))
    if request.method == 'POST':
        password = request.form.get('password') or ''
        confirm = request.form.get('confirm') or ''
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('reset_password.html')
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html')
        import secrets as _s
        salt = _s.token_bytes(16)
        from ..auth import hash_password
        user.salt = salt.hex()
        user.password_hash = hash_password(password, salt.hex())
        user.reset_token = None
        db.session.commit()
        flash('Password updated. Sign in with your new password.', 'success')
        return redirect(url_for('pages.login'))
    return render_template('reset_password.html')


@pages_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('pages.home'))


# ── Account & dashboard ──────────────────────────────────

@pages_bp.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    if request.method == 'POST':
        action = request.form.get('action')
        user = g.user
        if action == 'profile':
            user.display_name = (request.form.get('display_name') or '').strip() or user.username
            user.country = (request.form.get('country') or '').strip()
            user.bio = (request.form.get('bio') or '').strip()
            db.session.commit()
            flash('Profile updated.', 'success')
        elif action == 'password':
            current = request.form.get('current') or ''
            new = request.form.get('new') or ''
            if not check_password(current, user):
                flash('Current password is incorrect.', 'error')
            elif len(new) < 8:
                flash('New password must be at least 8 characters.', 'error')
            else:
                import secrets as _s
                from ..auth import hash_password
                salt = _s.token_bytes(16)
                user.salt = salt.hex()
                user.password_hash = hash_password(new, salt.hex())
                db.session.commit()
                flash('Password changed.', 'success')
        elif action == 'resend':
            if not user.email_verified:
                import secrets as _s
                user.verify_token = _s.token_urlsafe(32)
                db.session.commit()
                send_email(
                    user.email,
                    'Verify your Dirt Rally 3.0 account',
                    f"Confirm your email to activate your account:\n\n{_verify_link(user)}\n",
                )
                flash('Verification email sent.', 'info')
        return redirect(url_for('pages.account'))

    active_token = next((t for t in user_tokens(g.user) if t.revoked_at is None), None)
    unread = Notification.query.filter_by(user_id=g.user.id, read=False).count()
    return render_template(
        'account.html',
        verified=g.user.email_verified,
        token=active_token.token if active_token else '',
        unread=unread,
    )


def user_tokens(user: User) -> list[GameToken]:
    return user.tokens


@pages_bp.route('/dashboard')
@login_required
def dashboard():
    memberships = ClubMember.query.filter_by(user_id=g.user.id).all()
    my_clubs = [m.club for m in memberships if m.club is not None]
    my_club_ids = {m.club_id for m in memberships}
    events = [
        e for e in sort_events(get_all_events())
        if event_is_active(e) and e.get('club_id') in my_club_ids
    ]
    active_token = next(
        (t for t in g.user.tokens if t.revoked_at is None), None
    )
    unread = Notification.query.filter_by(user_id=g.user.id, read=False).count()
    return render_template(
        'dashboard.html',
        active_events=events,
        token=active_token.token if active_token else '',
        unread=unread,
        my_clubs=my_clubs,
    )


@pages_bp.route('/notifications')
@login_required
def notifications():
    items = (Notification.query
             .filter_by(user_id=g.user.id)
             .order_by(Notification.created_at.desc())
             .limit(200)
             .all())
    for n in items:
        n.read = True
    db.session.commit()
    return render_template('notifications.html', items=items)


# ── Placeholders (full pages land with M3/M4) ───────────

@pages_bp.route('/profile/<username>')
def profile(username: str):
    user = User.query.filter_by(username=username).first()
    if user is None:
        return render_template('not_found.html'), 404
    return render_template('profile.html', target=user)


@pages_bp.route('/event/<event_id>')
def event_detail(event_id: str):
    from ..events import entries_for_event, get_event
    from ..models import utcnow
    from ..results_view import (
        championship_rounds,
        effective_end,
        event_status,
        mode_label,
        overall_table,
        round_results,
        scoring_label,
    )
    from ..scoring import power_stage_enabled
    from ..tables import RX_LOCATIONS
    event = get_event(event_id)
    if event is None or event.get('system'):
        return render_template('not_found.html'), 404
    now = utcnow()
    entries = entries_for_event(event_id)
    rounds = championship_rounds(event, now)
    per_round = {r['index']: round_results(r, entries) for r in rounds}
    is_rx = (event.get('location') in RX_LOCATIONS
             or any(r['is_rx'] for r in rounds))
    ee = effective_end(event, now)
    return render_template(
        'event_detail.html', event=event, now=now,
        mode_label=mode_label(event), scoring_label=scoring_label(event),
        power_stage=power_stage_enabled(event), status=event_status(event, now),
        ends_in=ee.isoformat() if ee else '',
        rounds=rounds, per_round=per_round,
        standings=overall_table(event, entries), is_rx=is_rx)


@pages_bp.route('/install')
def install():
    return render_template('install.html')


@pages_bp.route('/download/DR3-windows.zip')
def download_client():
    zip_path = Path(__file__).resolve().parents[3] / 'dist' / 'DR3-windows.zip'
    if not zip_path.is_file():
        return render_template('not_found.html'), 404
    return send_file(zip_path, as_attachment=True, download_name='DR3-windows.zip')


@pages_bp.route('/about')
def about():
    return render_template('about.html')


@pages_bp.app_errorhandler(404)
def _not_found(_e):
    return render_template('not_found.html'), 404
