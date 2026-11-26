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
    auto_join_club,
    clear_cooldown,
    club_admins,
    club_invites,
    club_role,
    pending_requests,
    set_club_admins,
    set_club_invites,
    set_pending_requests,
)
from ..db import db
from ..events import (
    entries_for_event,
    event_is_active,
    event_is_upcoming,
    get_all_events,
    sort_events,
)
from ..models import (
    ChampionshipDraft,
    Club,
    ClubInvite,
    ClubMember,
    ClubStageState,
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


def _vehicle_class_groups() -> list[tuple[str, list[tuple[int, str]]]]:
    """Every catalog car grouped by class label, builder order (rally then RX),
    cars sorted by name within each group."""
    from ..scoring import VEHICLES
    from ..tables import ALL_CAR_CLASSES
    from dr2server.game_data import VEHICLE_CLASSES, vehicle_class_id_for_label

    vehicles = sorted(VEHICLES.items(), key=lambda kv: kv[1]['name'].lower())
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
    groups = [(label, by_class[label]) for label in ordered_labels if label in by_class]
    groups += [(label, by_class[label]) for label in sorted(by_class)
               if label not in ordered_labels]
    return groups


def _recent_stage_times(offset: int = 0, limit: int | None = None) -> list[dict]:
    """Every posted stage time as one row, newest first (backlog #42).

    Each row is a single submitted stage slot (the latest submission for that
    stage), so the front page's recent-times box can page through all of them."""
    from ..countries import flag_url
    from ..results_view import _run_attrs, _spare_tyres, format_ms
    from ..scoring import (
        car_label,
        fresh_damage_indices,
        fresh_tyre_start,
        tyre_set_changed,
    )

    club_name = {c.id: c.name for c in Club.query.all()}
    users = User.query.all()
    display_name = {u.username: u.display_name for u in users}
    country_of = {u.username: (u.country or '') for u in users}
    event_of = {e.id: e for e in Event.query.all()}

    # Latest StageRun per (event, driver, flat stage) — the same source the
    # club board reads tyre/input/camera/assists from.
    runs_of = {}
    for run in StageRun.query.all():
        key = (run.event_id, run.username, run.flat_stage_index)
        if None in key:
            continue
        cur = runs_of.get(key)
        if cur is None or (run.received_at, run.id) > (cur.received_at, cur.id):
            runs_of[key] = run
    # Tuning blobs + damage snapshots per slot — the spares fallback for
    # pre-v0.1.74 clients and the tyre-click popup payload.
    tuning_of = {}
    damage_of = {}
    for st in ClubStageState.query.all():
        if st.tuning_setup_b64:
            tuning_of[(st.event_id, st.username, st.flat_stage_index)] = \
                st.tuning_setup_b64
        if st.comp_damage_json:
            damage_of[(st.event_id, st.username, st.flat_stage_index)] = \
                json.loads(st.comp_damage_json)

    # Flat ordinals where damage starts from zero per event (first stage +
    # each rally-season round — rounds are separate in-game events).
    fresh_of: dict[str, set[int]] = {}

    def _fresh(eid: str) -> set[int]:
        rs = fresh_of.get(eid)
        if rs is None:
            ev = event_of.get(eid)
            rs = fresh_damage_indices(ev.to_dict() if ev else {})
            fresh_of[eid] = rs
        return rs

    rows = []
    for r in Result.query.all():
        ev = event_of.get(r.event_id)
        club = club_name.get(ev.club_id, '') if ev and ev.club_id else ''
        car = car_label({'vehicle_id': r.vehicle_id, 'car': r.car})
        stages = json.loads(r.stages_json or '[]')
        for i, s in enumerate(stages):
            t = int(s.get('time_ms', 0) or 0)
            sa = s.get('submitted_at')
            # A missing submitted_at is the cron round-expiry DNF marker —
            # those never happened on stage and are not listed; game-flow
            # close-outs (incl. terminal damage, stored at the stage limit)
            # count as driven.
            if t <= 0 or not sa:
                continue
            a = _run_attrs(runs_of.get((r.event_id, r.username, i)))
            d = damage_of.get((r.event_id, r.username, i))
            d0 = None
            if i not in _fresh(r.event_id):
                d0 = damage_of.get((r.event_id, r.username, i - 1))
                # A set fitted at the service before this stage starts with
                # fresh per-tyre state (wear + impact zeroed; the rest of the
                # car keeps its carried-over damage).
                if d0 is not None and i > 0 \
                        and tyre_set_changed(stages[i - 1], s):
                    d0 = fresh_tyre_start(d0)
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
                'tyres': [a['tyre']] if a['tyre'] is not None else [],
                # Parallel to tyres: the stage-state damage snapshot for this
                # slot (null when never captured — the popup shows its
                # no-data fallback, same as the club board) plus the start
                # state from the previous stage of the same run — except at
                # a fresh-damage ordinal (event start / season round start).
                'tyre_damage': ([{'s': i, 'd': d, 'd0': d0}]
                                if a['tyre'] is not None else []),
                'spares': _spare_tyres({
                    'spare_tyres': s.get('spare_tyres'),
                    'tuning_setup_b64':
                        tuning_of.get((r.event_id, r.username, i)),
                }),
                'input': a['input'],
                'vr': a['vr'],
                'camera': a['camera'],
                'assists': a['assists'],
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

    from sqlalchemy import func

    # Same definitions as /drivers + the profile pages: every recorded run
    # (club, official, time trial) counts as driven; only clean finishes count
    # toward the survival rate — DNF markers stored at their stage limit are
    # driven but not finished.
    stages_done = StageRun.query.filter(StageRun.player_finished.is_(True)).count()
    stages_driven = StageRun.query.count()
    survival_rate = (f'{stages_done / stages_driven * 100:.1f}%'
                     if stages_driven else '—')
    total_distance_m = int(db.session.query(func.sum(StageRun.distance_m))
                           .scalar() or 0)

    # Any non-zero race_status is a terminal close-out (wreck / retirement —
    # the game has sent 4, 5 and 6). The submitted_at requirement keeps cron
    # round-expiry DNFs out: those are flagged by events_generator without a
    # timestamp because the car was never destroyed.
    last_terminal_at = None
    last_terminal_user = None
    for r in Result.query.all():
        for s in json.loads(r.stages_json or '[]'):
            if int(s.get('race_status', 0) or 0) == 0:
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
        'stages_driven': stages_driven,
        'survival_rate': survival_rate,
        'total_distance_km': total_distance_m / 1000.0,
        'total_distance_miles': total_distance_m / 1000.0 * 0.621371,
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
            'car_url': url_for('pages.vehicles_index', vehicle=row['vehicle_id']) if row['vehicle_id'] else '',
            'time_text': row['time_text'],
            'at_text': _timeago(dt) if dt else '',
            'tyres': row['tyres'],
            'tyre_damage': row['tyre_damage'],
            'spares': row['spares'],
            'input': row['input'],
            'vr': row['vr'],
            'camera': row['camera'],
            'assists': row['assists'],
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
        auto_club = auto_join_club(user)
        send_email(
            user.email,
            'Verify your DR3 account',
            f"Hi {user.display_name},\n\n"
            f"Confirm your email to activate your account:\n\n{_verify_link(user)}\n",
        )
        if auto_club is not None:
            flash(f"Account created — you're now a member of {auto_club.name}. "
                  'Check your inbox to verify your email.', 'info')
        else:
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
                'Reset your DR3 password',
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
            if request.form.get('view_profile'):
                return redirect(url_for('pages.profile', username=user.username))
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
                    'Verify your DR3 account',
                    f"Confirm your email to activate your account:\n\n{_verify_link(user)}\n",
                )
                flash('Verification email sent.', 'info')
        return redirect(url_for('pages.account'))

    active_token = next((t for t in user_tokens(g.user) if t.revoked_at is None), None)
    unread = Notification.query.filter_by(user_id=g.user.id, read=False).count()
    # Favorite pickers: every confirmed car + every known location, grouped for
    # easier selection — cars by class (builder order), locations by discipline.
    from ..scoring import VEHICLES  # game_data catalog (sys.path set there)
    from dr2server.game_data import Location

    vehicle_ids = set(VEHICLES)
    car_groups = _vehicle_class_groups()
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
    # The per-stage heavy payloads are keyed by username too — drop them with
    # the results so an account deletion leaves no orphaned rows behind.
    for s in ClubStageState.query.filter_by(username=user.username).all():
        db.session.delete(s)
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
    from ..results_view import (
        current_event_scope,
        effective_end,
        event_profile,
        mode_of,
        scope_participation,
    )
    from ..tables import CHAMP_MODE_SEASON

    now = utcnow()
    memberships = ClubMember.query.filter_by(user_id=g.user.id).all()
    my_clubs = [m.club for m in memberships if m.club is not None]
    club_by_id = {c.id: c for c in my_clubs}
    # The "My clubs" panel only lists clubs the user manages (owner or admin);
    # plain memberships still count for the live-events table above.
    admin_clubs = [c for c in my_clubs
                   if club_role(c, g.user.username) in ('owner', 'admin')]
    events = []
    for e in sort_events(get_all_events()):
        club = club_by_id.get(e.get('club_id'))
        if not (event_is_active(e) and club is not None):
            continue
        prof = event_profile(e, now) or {}
        # Current window: the active leg for seasons, the whole shared window
        # otherwise.  Drives both the countdown and the Drivers column.
        offset, count = current_event_scope(e, now)
        entries = entries_for_event(e['id'])
        # "Done" = every stage in the window is closed: a genuine finish OR a
        # terminal DNF (wrecked/skipped stages can never be driven again, so
        # they don't keep the dot yellow once the rest of the event is over).
        # "Started" = any stage in the window carries a time or DNF marker.
        drivers_done = 0
        drivers_started = 0
        for en in entries:
            if not count:
                break
            _f, closed, touched = scope_participation(en, offset, count)
            if touched:
                drivers_started += 1
            if closed == count:
                drivers_done += 1
        my_entry = next((en for en in entries
                         if en.get('username') == g.user.username), None)
        if count:
            _my_finished, my_closed, my_touched = (
                scope_participation(my_entry, offset, count)
                if my_entry else (0, 0, False))
            driver_status = ('green' if my_closed == count
                             else 'yellow' if my_touched else 'red')
            start_status = 'green' if my_touched else 'red'
        else:
            driver_status = 'red'
            start_status = 'red'
        events.append({
            **e,
            'mode_label': ('Rally Season' if mode_of(e) == CHAMP_MODE_SEASON
                           else 'Multi-Location'),
            # Current event = the active leg's window end (seasons switch legs;
            # multi/legacy share one window, so it equals the stored end).
            'current_end': effective_end(e, now),
            # Class of the currently active leg, not the first stored one.
            'car_class': prof.get('car_class') or e.get('car_class'),
            # Same for location: the active leg's venue (seasons) or all
            # venues (multi/legacy), not the first stored one.
            'location': prof.get('location') or e.get('location'),
            'driver_status': driver_status,
            'start_status': start_status,
            'drivers_done': drivers_done,
            'drivers_started': drivers_started,
            'club_members': len(club.members),
        })
    active_token = next(
        (t for t in g.user.tokens if t.revoked_at is None), None
    )
    unread = Notification.query.filter_by(user_id=g.user.id, read=False).count()
    return render_template(
        'dashboard.html',
        active_events=events,
        token=active_token.token if active_token else '',
        unread=unread,
        admin_clubs=admin_clubs,
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

    stats = (db.session.query(StageRun.username, func.count(StageRun.id),
                              func.sum(StageRun.distance_m))
             .group_by(StageRun.username).all())
    by_user = {u: (int(n), int(d or 0)) for u, n, d in stats}
    finished = (db.session.query(StageRun.username, func.count(StageRun.id))
                .filter(StageRun.player_finished.is_(True))
                .group_by(StageRun.username).all())
    by_user_done = {u: int(n) for u, n in finished}
    users = User.query.all()
    rows = []
    for u in users:
        n, dist_m = by_user.get(u.username, (0, 0))
        done = by_user_done.get(u.username, 0)
        rows.append({
            'username': u.username,
            'display_name': u.display_name,
            'country': u.country or '',
            'stages': n,
            # Same definition as the profile page's pct_completed.
            'completed': f'{round(done / n * 100)}%' if n else '—',
            'distance_m': dist_m,
            'distance_km': dist_m / 1000.0,
            'created_at': u.created_at or utcnow(),
        })
    rows.sort(key=lambda r: (-r['distance_m'], r['created_at']))
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

    def _car_label(vid) -> str:
        """Car name; the class in parentheses only when another catalog car
        shares the same name."""
        from ..scoring import VEHICLES

        title, class_label = vehicle_meta(vid)
        if not class_label:
            return title
        same_name = sum(1 for m in VEHICLES.values() if str(m['name']) == title)
        return f'{title} ({class_label})' if same_name > 1 else title

    def _loc_label(lid) -> str:
        try:
            return Location(int(lid)).display_name
        except ValueError:
            return f'Location {lid}'

    def _top_driven(column, n=10):
        """Top cars/locations by total distance, only entries with >0 km."""
        rows = (db.session.query(column, func.sum(StageRun.distance_m),
                                 func.count(StageRun.id))
                .filter(StageRun.username == user.username, column.isnot(None))
                .group_by(column).all())
        scored = [(v, int(d or 0), int(c or 0)) for v, d, c in rows if int(d or 0) > 0]
        scored.sort(key=lambda r: (-r[1], -r[2]))
        return [(v, d) for v, d, _c in scored[:n]]

    top_cars = [{'label': _car_label(v), 'car': v, 'km': d / 1000.0}
                for v, d in _top_driven(StageRun.vehicle_id)]
    top_locations = [{'label': _loc_label(v), 'km': d / 1000.0}
                     for v, d in _top_driven(StageRun.location_id)]

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

    fav_car = _car_label(user.favorite_car_id) if user.favorite_car_id else ''
    fav_loc = _loc_label(user.favorite_location_id) if user.favorite_location_id else ''

    return {
        'runs': total,
        'pct_completed': round(finished / total * 100),
        'distance_km': distance_m / 1000.0,
        'top_cars': top_cars,
        'top_locations': top_locations,
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


@pages_bp.route('/vehicles')
def vehicles_index():
    """Vehicle page: pick a vehicle (?vehicle=<vehicle id>) for its stats + leaderboards.
    Unknown/missing ids fall back to the first catalog vehicle."""
    from ..scoring import VEHICLES

    groups = _vehicle_class_groups()
    selected = None
    raw = (request.args.get('vehicle') or request.args.get('car') or '').strip()
    if raw.isdigit():
        cand = int(raw)
        if cand in VEHICLES:
            selected = cand
    if selected is None and groups:
        selected = groups[0][1][0][0]
    data = _car_page_data(selected) if selected is not None else {}
    return render_template('car.html', car_groups=groups, **data)


@pages_bp.route('/vehicles/<int:vehicle_id>')
def vehicle_redirect(vehicle_id: int):
    """Convenience: /vehicles/<id> redirects to the ?vehicle= page."""
    return redirect(url_for('pages.vehicles_index', vehicle=vehicle_id), code=302)


@pages_bp.route('/cars')
def cars_legacy():
    """Back-compat: old /cars links redirect to /vehicles (query preserved)."""
    vid = (request.args.get('car') or request.args.get('vehicle') or '').strip()
    if vid.isdigit():
        return redirect(url_for('pages.vehicles_index', vehicle=int(vid)), code=302)
    return redirect(url_for('pages.vehicles_index'), code=302)


@pages_bp.route('/cars/<int:vehicle_id>')
def cars_legacy_id(vehicle_id: int):
    """Back-compat: old /cars/<id> links redirect to the ?vehicle= page."""
    return redirect(url_for('pages.vehicles_index', vehicle=vehicle_id), code=302)


@pages_bp.route('/api/vehicles/data')
def vehicles_data():
    """JSON for the vehicle page's live switcher (?vehicle=<vehicle id>)."""
    from ..scoring import VEHICLES

    raw = (request.args.get('vehicle') or request.args.get('car') or '').strip()
    if not raw.isdigit() or int(raw) not in VEHICLES:
        return jsonify({'ok': False, 'error': 'Unknown vehicle.'}), 404
    return jsonify({'ok': True, **_car_page_data(int(raw))})


def _car_page_data(vid: int) -> dict:
    """Everything the car page renders for one catalog vehicle id."""
    from ..cars_data import (
        car_image_url,
        car_specs,
        distance_driven_by_user,
        fastest_stages_by_location,
        terminal_stats,
    )
    from ..scoring import VEHICLES, vehicle_meta

    title, class_label = vehicle_meta(vid)
    specs = car_specs(vid)
    terminal = terminal_stats(vid)

    stats: list[list] = [['Car Class', class_label]]
    if specs:
        if specs.get('year'):
            stats.append(['Year', specs['year']])
        if specs.get('power') is not None:
            stats.append(['Power', f"{specs['power']} hp"])
        if specs.get('weight') is not None:
            stats.append(['Weight', f"{specs['weight']:,} kg"])
        if specs.get('engine_cc') is not None:
            cyl = f" · {specs['cyl']} cyl" if specs.get('cyl') else ''
            stats.append(['Engine', f"{specs['engine_cc']:,} cc{cyl}"])
        if specs.get('aspiration'):
            stats.append(['Aspiration', specs['aspiration']])
        if specs.get('transmission'):
            stats.append(['Gearbox', specs['transmission']])
    if terminal['total']:
        pct = terminal['dnf'] / terminal['total'] * 100
        stats.append(['Terminal damage',
                      f"{pct:.1f}% ({terminal['dnf']} of {terminal['total']})"])

    distance = distance_driven_by_user(vid)
    for row in distance:
        row['km_text'] = f"{row['km']:,.1f}"
    fastest = fastest_stages_by_location(vid)
    return {
        'vehicle_id': int(vid),
        'title': title,
        'class_label': class_label,
        'class_id': VEHICLES.get(vid, {}).get('class'),
        'image_url': car_image_url(vid),
        'specs': specs,
        'terminal': terminal,
        'stats': stats,
        'fastest_class': fastest['in_class'],
        'fastest_overall': fastest['overall'],
        'distance': distance,
    }


@pages_bp.route('/leaderboard')
def leaderboard():
    """Site-wide personal-best leaderboards: best time per driver on a stage +
    car (all run types) or total distance driven in a car (?mode=distance)."""
    from ..leaderboard import (
        CAMERA_POSITIONS,
        INPUT_METHODS,
        best_times_board,
        car_options,
        compound_options,
        distance_board,
        stage_info,
        stage_options,
    )
    from ..scoring import VEHICLES, vehicle_meta
    from dr2server.game_data import (
        SURFACE_DEGRAD_LEVELS,
        VEHICLE_CLASSES,
        Location,
        Track,
        stage_conditions_options_for_location,
    )

    stages = stage_options()
    cars = car_options()

    def _int_arg(name):
        raw = (request.args.get(name) or '').strip()
        return int(raw) if raw.isdigit() else None

    def _float_arg(name, allowed):
        raw = (request.args.get(name) or '').strip()
        try:
            v = float(raw)
        except ValueError:
            return None
        return v if any(abs(v - lvl) < 1e-9 for _, lvl in allowed) else None

    def _bool_arg(name):
        raw = (request.args.get(name) or '').strip()
        if raw == '1':
            return True
        if raw == '0':
            return False
        return None

    def _str_arg(name, allowed):
        raw = (request.args.get(name) or '').strip()
        return raw if raw in allowed else None

    mode = 'distance' if request.args.get('mode') == 'distance' else 'times'
    track = _int_arg('stage')
    car_arg = (request.args.get('car') or '').strip()
    vehicle_id = int(car_arg) if car_arg.isdigit() else None
    # "class-<cid>" selects every catalog car of that class.
    car_class_id = None
    vehicle_ids = None
    if car_arg.startswith('class-'):
        tail = car_arg[len('class-'):]
        if tail.isdigit():
            car_class_id = int(tail)
            vehicle_ids = sorted(vid for vid, meta in VEHICLES.items()
                                 if int(meta['class']) == car_class_id)
    conditions = _int_arg('conditions')
    compound = _int_arg('compound')
    degrad_level = _float_arg('degrad', SURFACE_DEGRAD_LEVELS)
    input_method = _str_arg('input', set(INPUT_METHODS))
    camera = _str_arg('camera', set(CAMERA_POSITIONS))
    vr = _bool_arg('vr')
    assists = _bool_arg('assists')
    hardcore = _bool_arg('hardcore')
    country = (request.args.get('country') or '').strip() or None

    # Two-select location/stage UI: the form always submits BOTH selects, so a
    # just-picked Location still carries the previous Stage — on that conflict
    # the picked location wins (its first stage is auto-selected). ?stage=
    # alone still works; a location without a valid stage of its own falls back
    # to the first stage of that location (the only one for RX).
    _loc_by_name = {l.display_name: l for l in Location}
    loc_sel = None
    raw_loc = (request.args.get('loc') or '').strip()
    if raw_loc.isdigit():
        try:
            loc_sel = Location(int(raw_loc))
        except ValueError:
            loc_sel = None
    track_loc = None
    if track is not None:
        try:
            track_loc = Track(int(track)).location
        except (ValueError, TypeError):
            pass  # out-of-catalog stage id: keep it for the query, no location
    if track_loc is not None and loc_sel is not None and track_loc is not loc_sel:
        track = None  # stale stage from another location — the picked one wins
    if track is not None and track_loc is not None:
        loc_sel = track_loc
    elif loc_sel is not None:
        opts = next((o for n, o in stages if _loc_by_name.get(n) is loc_sel), [])
        track = opts[0][0] if opts else None
    if track is None and stages:
        track = stages[0][1][0][0]
    # Select options: every location with catalog stages, grouped by
    # discipline for the optgrouped select + the stage list of the resolved
    # location.
    try:
        cur_loc = Track(int(track)).location if track is not None else None
    except (ValueError, TypeError):
        cur_loc = None  # out-of-catalog stage id

    rally_locs, rx_locs, stage_opts = [], [], []
    for name, opts in stages:
        l = _loc_by_name.get(name)
        if l is None:
            continue
        (rx_locs if l.discipline == 'rallycross' else rally_locs).append((int(l), name))
        if cur_loc is not None and l is cur_loc:
            stage_opts = opts
    loc_groups = [('Rally', rally_locs), ('Rallycross', rx_locs)]

    # Weather options are scoped to the selected stage's location — the same
    # per-location lighting sweep the club event creation offers. A selection
    # that is not available on this stage is dropped rather than filtering the
    # board down to nothing.
    conditions_options = []
    if track is not None:
        try:
            conditions_options = stage_conditions_options_for_location(
                Track(int(track)).location)
        except (ValueError, TypeError):
            conditions_options = []
    if conditions is not None and \
            conditions not in {cid for cid, _ in conditions_options}:
        conditions = None

    board = {}
    if mode == 'times' and track is not None:
        board = best_times_board(
            track, vehicle_id, vehicle_ids=vehicle_ids, conditions=conditions,
            compound=compound, degrad_level=degrad_level,
            input_method=input_method, camera=camera, vr=vr, assists=assists,
            hardcore=hardcore, country=country)
    elif mode == 'distance':
        board = distance_board(
            vehicle_id, vehicle_ids=vehicle_ids, degrad_level=degrad_level,
            input_method=input_method, camera=camera, vr=vr, assists=assists,
            hardcore=hardcore, country=country)

    # Vehicle names link to the car page with that car pre-selected — only
    # for catalog ids (legacy rows store 0/unknown and stay plain text).
    for row in board.get('rows', []):
        vid = row.get('vehicle_id')
        row['car_url'] = url_for('pages.vehicles_index', vehicle=vid) if vid in VEHICLES else ''

    # Distance mode (and an empty times board) carries no split metadata.
    board.setdefault('split_count', 0)
    board.setdefault('split_word', 'Split')
    board.setdefault('is_rx', False)

    car_name, car_class = ('', '')
    if vehicle_id is not None and vehicle_id in VEHICLES:
        car_name, car_class = vehicle_meta(vehicle_id)
    elif car_class_id is not None:
        car_name = VEHICLE_CLASSES.get(car_class_id, f'Class {car_class_id}')

    countries = sorted({u.country for u in User.query.all() if u.country})

    return render_template(
        'leaderboard.html',
        mode=mode,
        loc_groups=loc_groups,
        cur_loc=int(cur_loc) if cur_loc is not None else None,
        stage_opts=stage_opts,
        cars=cars,
        class_ids={label: int(cid) for cid, label in VEHICLE_CLASSES.items()},
        track=track,
        vehicle_id=vehicle_id,
        car_class_id=car_class_id,
        stage_info=stage_info(track) if track else {},
        car_name=car_name,
        car_class=car_class,
        conditions_options=conditions_options,
        filters_active=any(v is not None for v in (
            conditions, compound, degrad_level, input_method, camera, vr,
            assists, hardcore, country)),
        compound_opts=compound_options(),
        degrad_options=SURFACE_DEGRAD_LEVELS,
        camera_options=CAMERA_POSITIONS,
        input_options=INPUT_METHODS,
        country_options=countries,
        sel_conditions=conditions,
        sel_compound=compound,
        sel_degrad=degrad_level,
        sel_input=input_method,
        sel_camera=camera,
        sel_vr=vr,
        sel_assists=assists,
        sel_hardcore=hardcore,
        sel_country=country,
        **board)


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
