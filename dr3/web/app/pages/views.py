"""Web pages (session auth). Functional first pass — visual redesign at M4."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    jsonify,
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
from ..clubs import (
    clear_cooldown,
    club_admins,
    club_invites,
    pending_requests,
    set_club_admins,
    set_club_invites,
    set_pending_requests,
)
from ..db import db
from ..events import event_is_active, event_is_upcoming, get_all_events, sort_events
from ..models import (
    ChampionshipDraft,
    Club,
    ClubInvite,
    ClubMember,
    Event,
    GameToken,
    Notification,
    Result,
    StageRun,
    TimeTrialEntry,
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


def _recent_stage_times(offset: int = 0, limit: int | None = None) -> list[dict]:
    """Every posted stage time as one row, newest first (backlog #42).

    Each row is a single submitted stage slot (the latest submission for that
    stage), so the front page's recent-times box can page through all of them."""
    from ..countries import flag_url
    from ..results_view import format_ms
    from ..scoring import car_label

    club_name = {c.id: c.name for c in Club.query.all()}
    users = User.query.all()
    display_name = {u.username: u.display_name for u in users}
    country_of = {u.username: (u.country or '') for u in users}
    event_of = {e.id: e for e in Event.query.all()}

    rows = []
    for r in Result.query.all():
        ev = event_of.get(r.event_id)
        club = club_name.get(ev.club_id, '') if ev and ev.club_id else ''
        car = car_label({'vehicle_id': r.vehicle_id, 'car': r.car})
        for s in json.loads(r.stages_json or '[]'):
            t = int(s.get('time_ms', 0) or 0)
            sa = s.get('submitted_at')
            if t <= 0 or not sa:
                continue
            rows.append({
                'driver': display_name.get(r.username, r.username),
                'driver_username': r.username,
                'driver_flag': flag_url(country_of.get(r.username, '')),
                'club': club,
                'club_id': ev.club_id if ev and ev.club_id else '',
                'car': car,
                'vehicle_id': r.vehicle_id,
                'time_text': format_ms(t),
                'at': sa,
            })
    rows.sort(key=lambda x: x['at'], reverse=True)
    if limit is not None:
        return rows[offset:offset + limit]
    return rows[offset:]


def _home_stats(now) -> dict:
    """Site-wide stats for the front page (backlog #29): driver/club counts,
    total completed stages, and the most recent stage times players posted."""
    events = Event.query.all()

    active_clubs = set()
    for e in events:
        if not e.club_id:
            continue
        ed = e.to_dict()
        if event_is_active(ed, now) or event_is_upcoming(ed, now):
            active_clubs.add(e.club_id)

    stages_done = 0
    for r in Result.query.all():
        for s in json.loads(r.stages_json or '[]'):
            if int(s.get('time_ms', 0) or 0) > 0:
                stages_done += 1

    last_terminal_at = None
    last_terminal_user = None
    for r in Result.query.all():
        for s in json.loads(r.stages_json or '[]'):
            if int(s.get('race_status', 0) or 0) not in (4, 5):
                continue
            sa = s.get('submitted_at')
            if sa and (last_terminal_at is None or sa > last_terminal_at):
                last_terminal_at, last_terminal_user = sa, r.username

    last_terminal = None
    if last_terminal_at:
        from datetime import datetime

        at = datetime.fromisoformat(last_terminal_at)
        owner = User.query.filter_by(username=last_terminal_user).first()
        last_terminal = {
            'days': max((now - at).days, 0),
            'driver': owner.display_name if owner else last_terminal_user,
        }

    return {
        'drivers': User.query.count(),
        'clubs': Club.query.count(),
        'active_clubs': len(active_clubs),
        'stages_done': stages_done,
        'last_terminal': last_terminal,
        'recent': _recent_stage_times(0, 10),
    }


@pages_bp.route('/')
def home():
    return render_template('home.html', stats=_home_stats(utcnow()))


@pages_bp.route('/api/home/recent-times')
def home_recent_times():
    """Most recent posted stage times for the front page's infinite-scroll
    box (public). ``offset``/``limit`` paginate rows; a short last page is
    the end-of-list signal."""
    from .. import _parse_dt, _timeago

    try:
        offset = max(int(request.args.get('offset', 0)), 0)
        limit = min(max(int(request.args.get('limit', 25)), 1), 100)
    except ValueError:
        offset, limit = 0, 25
    rows = []
    for row in _recent_stage_times(offset, limit):
        dt = _parse_dt(row['at'])
        rows.append({
            'driver': row['driver'],
            'driver_flag': row.get('driver_flag', ''),
            'driver_url': url_for('pages.profile', username=row['driver_username']),
            'club': row['club'],
            'club_url': url_for('clubs.club_detail', club_id=row['club_id']) if row['club_id'] else '',
            'car': row['car'],
            'car_url': url_for('pages.car_stats', vehicle_id=row['vehicle_id']) if row['vehicle_id'] else '',
            'time_text': row['time_text'],
            'at_text': _timeago(dt) if dt else '',
        })
    return jsonify({'ok': True, 'rows': rows})


# ── Auth ─────────────────────────────────────────────────

def _login_next() -> str:
    """Post-login destination: the requested ``next`` if it is a local path,
    with the subpath prefix restored (links generated before the fix carry a
    bare path), else the dashboard."""
    nxt = request.args.get('next') or ''
    if not nxt.startswith('/') or '://' in nxt or nxt.startswith('//'):
        return url_for('pages.dashboard')
    root = request.script_root.rstrip('/')
    if root and not nxt.startswith(root + '/'):
        nxt = root + nxt
    return nxt


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
        return redirect(_login_next())
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

            def _opt_id(key: str):
                raw = (request.form.get(key) or '').strip()
                try:
                    return int(raw) if raw else None
                except ValueError:
                    return None

            user.favorite_car_id = _opt_id('favorite_car_id')
            user.favorite_location_id = _opt_id('favorite_location_id')
            user.show_device_name = request.form.get('show_device_name') == 'on'
            user.show_days_played = request.form.get('show_days_played') == 'on'
            user.show_seconds_played = request.form.get('show_seconds_played') == 'on'
            user.show_sessions = request.form.get('show_sessions') == 'on'
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
    # Favorite pickers: every confirmed car + every known location, grouped for
    # easier selection — cars by class (builder order), locations by discipline.
    from ..scoring import VEHICLES  # game_data catalog (sys.path set there)
    from ..tables import ALL_CAR_CLASSES
    from dr2server.game_data import Location, VEHICLE_CLASSES, vehicle_class_id_for_label
    vehicles = sorted(VEHICLES.items(), key=lambda kv: kv[1]['name'].lower())
    vehicle_ids = {vid for vid, _ in vehicles}
    by_class: dict[str, list] = {}
    for vid, meta in vehicles:
        label = VEHICLE_CLASSES.get(int(meta['class']), f"Class {meta['class']}")
        by_class.setdefault(label, []).append((vid, meta['name']))
    # Builder class order (rally then RX), mapped to canonical enum labels.
    ordered_labels: list[str] = []
    for key in ALL_CAR_CLASSES:
        cid = vehicle_class_id_for_label(key)
        if cid is not None:
            label = VEHICLE_CLASSES[cid]
            if label not in ordered_labels:
                ordered_labels.append(label)
    car_groups = [(label, by_class[label]) for label in ordered_labels if label in by_class]
    car_groups += [(label, by_class[label]) for label in sorted(by_class)
                   if label not in ordered_labels]
    locations = sorted(Location, key=lambda loc: loc.display_name.lower())
    location_ids = {int(loc) for loc in locations}
    location_groups = [
        ('Rally', [loc for loc in locations if loc.discipline != 'rallycross']),
        ('RX', [loc for loc in locations if loc.discipline == 'rallycross']),
    ]
    return render_template(
        'account.html',
        verified=g.user.email_verified,
        token=active_token.token if active_token else '',
        unread=unread,
        vehicle_ids=vehicle_ids,
        car_groups=car_groups,
        location_ids=location_ids,
        location_groups=location_groups,
    )


@pages_bp.route('/account/delete', methods=['GET', 'POST'])
@login_required
def account_delete():
    if request.method == 'POST':
        return _delete_account(g.user)
    memberships = ClubMember.query.filter_by(user_id=g.user.id).all()
    owned_ids = {c.id for c in Club.query.filter_by(created_by=g.user.id).all()}
    member_clubs = [m.club for m in memberships if m.club is not None]
    return render_template(
        'account_delete.html',
        user=g.user,
        owned_clubs=[c for c in member_clubs if c.id in owned_ids],
        other_clubs=[c for c in member_clubs if c.id not in owned_ids],
        result_count=Result.query.filter_by(username=g.user.username).count(),
        token_count=len([t for t in g.user.tokens if t.revoked_at is None]),
    )


def _delete_account(user: User):
    owned = Club.query.filter_by(created_by=user.id).all()
    if owned:
        names = ', '.join(c.name for c in owned)
        flash(
            f'You still own {len(owned)} club{"s" if len(owned) != 1 else ""} '
            f'({names}) — delete those clubs first.',
            'error',
        )
        return redirect(url_for('pages.account_delete'))
    for club in Club.query.all():
        admins = club_admins(club)
        if user.username in admins:
            set_club_admins(club, [a for a in admins if a != user.username])
        reqs = pending_requests(club)
        if any(r.get('username') == user.username for r in reqs):
            set_pending_requests(
                club, [r for r in reqs if r.get('username') != user.username])
        invs = club_invites(club)
        if any(i.get('username') == user.username for i in invs):
            set_club_invites(
                club, [i for i in invs if i.get('username') != user.username])
        clear_cooldown(club, user.username)
    for m in ClubMember.query.filter_by(user_id=user.id).all():
        db.session.delete(m)
    for r in Result.query.filter_by(username=user.username).all():
        db.session.delete(r)
    for t in TimeTrialEntry.query.filter_by(username=user.username).all():
        db.session.delete(t)
    for s in StageRun.query.filter_by(username=user.username).all():
        db.session.delete(s)
    for d in ChampionshipDraft.query.filter_by(owner=user.username).all():
        db.session.delete(d)
    for inv in ClubInvite.query.filter_by(created_by=user.id).all():
        db.session.delete(inv)
    session.pop('user_id', None)
    db.session.delete(user)
    db.session.commit()
    flash('Your account has been deleted.', 'success')
    return redirect(url_for('pages.login'))


def user_tokens(user: User) -> list[GameToken]:
    return user.tokens


@pages_bp.route('/dashboard')
@login_required
def dashboard():
    from ..results_view import mode_label

    memberships = ClubMember.query.filter_by(user_id=g.user.id).all()
    my_clubs = [m.club for m in memberships if m.club is not None]
    my_club_ids = {m.club_id for m in memberships}
    events = [
        {**e, 'mode_label': mode_label(e)}
        for e in sort_events(get_all_events())
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

@pages_bp.route('/drivers')
def drivers():
    """All registered drivers with their career stats, most active first.

    Stats are computed from the append-only stage_runs table (#56 — the stored
    users counters are retired; pre-stage_runs history is backfilled into the
    table by tools/repair_stage_runs_backfill.sh)."""
    from sqlalchemy import func

    club_count: dict[str, int] = {}
    for c in Club.query.all():
        for m in c.members:
            uname = m.user.username if m.user else None
            if uname:
                club_count[uname] = club_count.get(uname, 0) + 1
    stats = (db.session.query(StageRun.username, func.count(StageRun.id),
                              func.sum(StageRun.distance_m))
             .group_by(StageRun.username).all())
    by_user = {u: (int(n), int(d or 0)) for u, n, d in stats}
    users = User.query.order_by(User.display_name).all()
    rows = [{
        'username': u.username,
        'display_name': u.display_name,
        'country': u.country or '',
        'clubs': club_count.get(u.username, 0),
        'stages': by_user.get(u.username, (0, 0))[0],
        'distance_km': by_user.get(u.username, (0, 0))[1] / 1000.0,
    } for u in users]
    rows.sort(key=lambda r: (-r['stages'], r['display_name']))
    return render_template('drivers.html', rows=rows)


def _device_label_values(library, device, name) -> str:
    """Human input-device name (wheel name, else library/device fallback)."""
    if name:
        return name
    if library == 'lib_keyboard':
        return 'Keyboard'
    if library == 'lib_direct_input':
        return 'Wheel'
    return device or 'Unknown'


def _device_label(run: StageRun) -> str:
    """Human input-device name for a stage run (wheel name, else fallback)."""
    return _device_label_values(run.input_library, run.input_device, run.input_name)


def _format_play_seconds(total_s: int) -> str:
    h, rem = divmod(int(total_s or 0), 3600)
    m = rem // 60
    return f'{h}h {m}m' if h else f'{m}m'


def _profile_stats(user: User) -> dict:
    """Per-stage run stats aggregated from stage_runs.

    Empty dict when the driver has no recorded runs yet (the profile page then
    shows nothing). Values are computed, not stored — favorites aside, the
    profile always reflects the driver's full driving history on DR3."""
    from sqlalchemy import func, or_

    from ..scoring import vehicle_meta
    from dr2server.game_data import Location

    q = StageRun.query.filter_by(username=user.username)
    total = q.count()
    if not total:
        return {}

    finished = q.filter(StageRun.player_finished.is_(True)).count()
    distance_m = int(db.session.query(func.sum(StageRun.distance_m))
                     .filter_by(username=user.username).scalar() or 0)

    def _top(column):
        rows = (db.session.query(column, func.sum(StageRun.distance_m),
                                 func.count(StageRun.id))
                .filter(StageRun.username == user.username, column.isnot(None))
                .group_by(column).all())
        if not rows:
            return None, 0
        vid, dist, _n = max(rows, key=lambda r: (int(r[1] or 0), int(r[2])))
        return vid, int(dist or 0)

    car_id, car_km_m = _top(StageRun.vehicle_id)
    loc_id, loc_km_m = _top(StageRun.location_id)

    # Device + play counters come from the newest run that actually reported
    # them (half-merged rows carry none and must not blank the panel).
    latest_input = (q.filter(StageRun.input_library.isnot(None))
                    .order_by(StageRun.received_at.desc(), StageRun.id.desc()).first())
    latest_play = (q.filter(or_(StageRun.g_days_played > 0,
                                StageRun.g_seconds_played > 0,
                                StageRun.g_session_count > 0))
                   .order_by(StageRun.received_at.desc(), StageRun.id.desc()).first())

    # Per-device distance share (#57): group by hardware (library + device id),
    # label = the most common wheel name seen on that hardware. DNF/half rows
    # carry no trusted distance and drop out naturally.
    devices: dict[tuple, dict] = {}
    for lib, dev, name, dist in (
            db.session.query(StageRun.input_library, StageRun.input_device,
                             StageRun.input_name, StageRun.distance_m)
            .filter(StageRun.username == user.username,
                    StageRun.input_library.isnot(None),
                    StageRun.distance_m.isnot(None)).all()):
        g = devices.setdefault((lib, dev or ''), {'names': Counter(), 'dist': 0})
        if name:
            g['names'][name] += 1
        g['dist'] += int(dist)
    device_list = []
    total_dev_dist = sum(g['dist'] for g in devices.values())
    if total_dev_dist > 0:
        for (lib, dev), g in sorted(devices.items(), key=lambda kv: -kv[1]['dist']):
            label = (g['names'].most_common(1)[0][0] if g['names']
                     else _device_label_values(lib, dev or None, None))
            device_list.append({
                'label': label,
                'pct': round(g['dist'] / total_dev_dist * 100),
                'km': g['dist'] / 1000.0,
            })

    car_label = ''
    if car_id is not None:
        title, class_label = vehicle_meta(car_id)
        car_label = f'{title} ({class_label})' if class_label else title
    loc_label = ''
    if loc_id is not None:
        try:
            loc_label = Location(int(loc_id)).display_name
        except ValueError:
            loc_label = f'Location {loc_id}'

    fav_car, fav_loc = '', ''
    if user.favorite_car_id:
        title, class_label = vehicle_meta(user.favorite_car_id)
        fav_car = f'{title} ({class_label})' if class_label else title
    if user.favorite_location_id:
        try:
            fav_loc = Location(int(user.favorite_location_id)).display_name
        except ValueError:
            fav_loc = f'Location {user.favorite_location_id}'

    return {
        'runs': total,
        'pct_completed': round(finished / total * 100),
        'distance_km': distance_m / 1000.0,
        'top_car': car_label, 'top_car_km': car_km_m / 1000.0,
        'top_location': loc_label, 'top_location_km': loc_km_m / 1000.0,
        'fav_car': fav_car, 'fav_location': fav_loc,
        # Device + play counters from the newest run that reported them.
        'device': _device_label(latest_input) if latest_input else '',
        'devices': device_list,
        'days_played': int(latest_play.g_days_played or 0) if latest_play else 0,
        'seconds_played': (_format_play_seconds(latest_play.g_seconds_played or 0)
                           if latest_play else ''),
        'sessions': int(latest_play.g_session_count or 0) if latest_play else 0,
    }


@pages_bp.route('/profile/<username>')
def profile(username: str):
    user = User.query.filter_by(username=username).first()
    if user is None:
        return render_template('not_found.html'), 404
    return render_template(
        'profile.html', target=user, stats=_profile_stats(user))


@pages_bp.route('/cars/<int:vehicle_id>')
def car_stats(vehicle_id: int):
    """Super-simple per-car page (more stats land later)."""
    from ..scoring import vehicle_meta

    title, class_label = vehicle_meta(vehicle_id)
    return render_template('car.html', vehicle_id=vehicle_id,
                           title=title, class_label=class_label)


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
