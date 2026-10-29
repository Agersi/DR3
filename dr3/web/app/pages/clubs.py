"""Club pages: list/create/detail, membership, roles, invites and the
multi-event championship builder (Multi-Location Rally / Rally Season)."""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..auth import verified_required
from ..clubs import (
    COOLDOWN_AFTER_CANCEL,
    COOLDOWN_AFTER_DENY,
    add_member,
    add_notification,
    clear_cooldown,
    clear_invite_notification,
    clear_join_request_notification,
    club_busy_until,
    club_event_conflict,
    club_invites,
    club_is_visible_to,
    club_join_policy,
    club_role,
    club_reviewers,
    club_visibility,
    delete_draft,
    draft_from_event,
    email_invite,
    email_join_approved,
    email_join_denied,
    email_join_request,
    enqueue_request,
    find_invite_link,
    get_draft,
    get_user_by_name,
    pending_requests,
    remove_member,
    REQ_ALREADY_MEMBER,
    REQ_ALREADY_PENDING,
    REQ_COOLDOWN,
    REQ_OK,
    save_draft,
    set_club_admins,
    set_club_invites,
    set_cooldown,
    set_pending_requests,
    user_has_invite,
    user_has_pending_request,
    user_is_admin,
    user_is_member,
    user_is_owner,
)
from ..db import db
from ..events import (
    entries_for_event,
    event_is_active,
    event_is_upcoming,
    get_all_events,
    sort_events,
)
from ..models import ChampionshipDraft, Club, ClubInvite, Event, Result, User, utcnow
from ..results_view import (
    championship_rounds,
    effective_end,
    event_status,
    mode_label,
    overall_table,
    scoring_label,
)
from ..scoring import SCORING_SYSTEMS, SCORING_WRC, duration_label, power_stage_enabled
from ..tables import (
    ALL_CAR_CLASSES,
    CHAMP_MODE_MULTI,
    CHAMP_MODE_SEASON,
    CHAMP_MODES,
    CONDITIONS_BY_LOCATION,
    DEFAULT_CHAMP_SETTINGS,
    LOCATION_SURFACE,
    MAX_CHAMP_DURATION,
    MAX_CHAMP_EVENTS,
    MAX_EVENT_DAYS,
    MAX_RX_LAPS,
    MAX_STAGES_PER_EVENT,
    MIN_RX_LAPS,
    RX_LOCATIONS,
    SERVICE_AREA_OPTIONS,
    STAGE_CAPS,
    STAGE_ROUTES,
    STAGES,
    SURFACE_DEG_OPTIONS,
    blank_event,
    blank_stage,
    bucket_for_duration,
    championship_duration,
    default_conditions_id_for_location,
    enrich_stage,
    leg_duration,
    parse_championship_form,
    random_championship_events,
    to_int_or_zero,
    validate_championship,
)

clubs_bp = Blueprint('clubs', __name__)

_SAFE_USER_RE = re.compile(r'^[A-Za-z0-9_]{3,32}$')


def _load_club(club_id: str) -> Club:
    club = db.session.get(Club, club_id)
    if club is None:
        abort(404)
    return club


def _require_club_admin(club_id: str) -> Club:
    club = _load_club(club_id)
    if not user_is_admin(club, g.user.username):
        abort(403)
    return club


def _require_club_owner(club_id: str) -> Club:
    club = _load_club(club_id)
    if not user_is_owner(club, g.user.username):
        abort(403)
    return club


def _require_draft(club_id: str, draft_id: str) -> dict:
    draft = get_draft(draft_id)
    if not draft or draft.get('club_id') != club_id:
        abort(404)
    return draft


def _latest_club_event(club_id: str) -> dict | None:
    """The club's most recent championship (latest start_time, id tiebreak)."""
    events = [e for e in get_all_events()
              if e.get('club_id') == club_id and e.get('start_time')]

    def _ts(e):
        try:
            return datetime.fromisoformat(e['start_time']).timestamp()
        except (TypeError, ValueError):
            return 0.0

    return max(events, key=lambda e: (_ts(e), e['id'])) if events else None


def _championship_edit_context(club: Club, draft: dict) -> dict:
    return dict(
        club=club, draft=draft,
        rally_locs=sorted(loc for loc in STAGES if loc not in RX_LOCATIONS),
        rx_locs=sorted(loc for loc in STAGES if loc in RX_LOCATIONS),
        # All classes (rally + rallycross) are selectable for every event.
        car_classes=list(ALL_CAR_CLASSES.keys()),
        conditions_by_location=CONDITIONS_BY_LOCATION,
        # Per-location fallback pick for the conditions dropdown (closest to
        # Daytime / Clear / Dry that the location ships lighting for).
        default_condition_by_location={
            loc: default_conditions_id_for_location(loc) for loc in STAGES
        },
        surface_deg_options=SURFACE_DEG_OPTIONS,
        service_area_options=SERVICE_AREA_OPTIONS,
        # RX circuits run several lapped stages on their single verified route,
        # so the route-count cap (and its editor hint) doesn't apply to them.
        stage_routes=STAGE_ROUTES,
        stage_caps={k: v for k, v in STAGE_CAPS.items() if k not in RX_LOCATIONS},
        blank_stage=blank_stage(),
        max_events=MAX_CHAMP_EVENTS, max_stages=MAX_STAGES_PER_EVENT,
        max_event_days=MAX_EVENT_DAYS,
        min_rx_laps=MIN_RX_LAPS, max_rx_laps=MAX_RX_LAPS,
    )


# ── List & create ────────────────────────────────────────

def _club_event_maps(clubs: list[Club], now: datetime) -> dict[str, dict]:
    """Per visible club, the live/upcoming championship that expires soonest.

    Returns {club_id: {'event': event_dict, 'live': bool}} — one pass over all
    events so the list page stays a single query per table."""
    ids = {c.id for c in clubs}
    best: dict[str, tuple[dict, datetime]] = {}
    for e in get_all_events():
        cid = e.get('club_id')
        if not cid or cid not in ids:
            continue
        if not (event_is_active(e, now) or event_is_upcoming(e, now)):
            continue
        # Live seasons count by their current leg's window end, not the last
        # leg's stored end_time (effective_end).
        end = effective_end(e, now)
        if end is None:
            continue
        cur = best.get(cid)
        if cur is None or end < cur[1]:
            best[cid] = (e, end)
    return {cid: {'event': e, 'live': event_is_active(e, now),
                  'end': e2.isoformat()}
            for cid, (e, e2) in best.items()}


@clubs_bp.route('/clubs')
def clubs_list():
    user_id = session.get('user_id')
    user = db.session.get(User, user_id) if user_id else None
    all_clubs = [c for c in Club.query.order_by(Club.created_at.desc()).all()
                 if club_is_visible_to(c, user)]
    query = request.args.get('q', '').strip()
    if query:
        q = query.lower()
        all_clubs = [
            c for c in all_clubs
            if q in c.name.lower() or q in (c.description or '').lower()
        ]
    # Soonest-expiring championship first; clubs with no live/upcoming
    # championship go last, newest-created first within each group.
    now = utcnow()
    event_by_club = _club_event_maps(all_clubs, now)

    def sort_key(c):
        created = c.created_at.timestamp() if c.created_at else 0.0
        info = event_by_club.get(c.id)
        if info is None:
            return (1, 0.0, -created)
        try:
            end_ts = datetime.fromisoformat(info['end']).timestamp()
        except (TypeError, ValueError):
            end_ts = float('inf')
        return (0, end_ts, -created)

    all_clubs.sort(key=sort_key)
    return render_template('clubs.html', clubs=all_clubs, query=query,
                           logged_in=user is not None, event_by_club=event_by_club)


@clubs_bp.route('/clubs', methods=['POST'])
@verified_required
def create_club():
    name = (request.form.get('name') or '').strip()
    desc = (request.form.get('description') or '').strip()
    visibility = (request.form.get('visibility') or 'public').strip().lower()
    join_policy = (request.form.get('join_policy') or 'open').strip().lower()
    if visibility not in ('public', 'private'):
        visibility = 'public'
    if join_policy not in ('open', 'approval'):
        join_policy = 'open'
    if not name:
        flash('Club name is required.', 'error')
        return redirect(url_for('clubs.clubs_list'))
    if len(name) > 40:
        flash('Club name must be under 40 characters.', 'error')
        return redirect(url_for('clubs.clubs_list'))

    cid = f'club-{secrets.token_hex(4)}'
    club = Club(id=cid, name=name, description=desc, created_by=g.user.id,
                visibility=visibility, join_policy=join_policy)
    db.session.add(club)
    db.session.flush()
    add_member(club, g.user)
    db.session.commit()
    flash(f'Club "{name}" created!', 'success')
    return redirect(url_for('clubs.club_detail', club_id=cid))


# ── Detail ───────────────────────────────────────────────

@clubs_bp.route('/clubs/<club_id>')
def club_detail(club_id: str):
    club = _load_club(club_id)
    user_id = session.get('user_id')
    user = db.session.get(User, user_id) if user_id else None
    if not club_is_visible_to(club, user):
        abort(404)

    members = []
    for m in sorted(club.members, key=lambda m: (m.user.id if m.user else 0)):
        if m.user is not None:
            members.append({'user': m.user, 'role': club_role(club, m.user.username)})

    events = sort_events([e for e in get_all_events() if e.get('club_id') == club_id])
    live_event = next((e for e in events if event_is_active(e)), None)

    # Full results view per championship: rounds with windows/status plus the
    # overall standings (points first, bogey-adjusted time as tiebreaker).
    now = utcnow()
    championships = []
    for e in events:
        entries = entries_for_event(e['id'])
        ee = effective_end(e, now)
        championships.append({
            'event': e,
            'mode_label': mode_label(e),
            'scoring_label': scoring_label(e),
            'power_stage': power_stage_enabled(e),
            'status': event_status(e, now),
            # Live countdown counts to the CURRENT leg's window end for
            # seasons (effective_end), matching the list-page ordering.
            'ends_in': ee.isoformat() if ee else '',
            'rounds': championship_rounds(e, now),
            'standings': overall_table(e, entries),
        })

    uname = user.username if user else None
    is_admin = user_is_admin(club, uname)
    pending_users = []
    if is_admin:
        for r in pending_requests(club):
            pu = get_user_by_name(r.get('username', ''))
            if pu:
                pending_users.append({'user': pu, 'requested_at': r.get('requested_at', '')})
    pending_invites = []
    if is_admin:
        for inv in club_invites(club):
            iu = get_user_by_name(inv.get('username', ''))
            if iu:
                pending_invites.append({'user': iu, 'created_at': inv.get('created_at', '')})
    invite_links = []
    if is_admin:
        for link in ClubInvite.query.filter_by(club_id=club_id).all():
            if link.revoked_at is None:
                invite_links.append(link)

    # Draft management (backlog #45): the most recent championship to load as
    # a fresh draft, plus this user's existing drafts — available across every
    # club they admin; loading one from another club clones it into this one.
    previous_event = None
    my_drafts = []
    if is_admin:
        previous_event = _latest_club_event(club_id)
        if uname:
            rows = ChampionshipDraft.query.filter_by(owner=uname).all()
            rows.sort(key=lambda d: d.created_at or utcnow(), reverse=True)
            foreign_ids = {d.club_id for d in rows} - {club.id}
            source_names = {}
            if foreign_ids:
                source_names = {c.id: c.name for c in
                                Club.query.filter(Club.id.in_(foreign_ids)).all()}
            for d in rows:
                try:
                    data = json.loads(d.data_json or '{}')
                except ValueError:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                evs = [ev for ev in (data.get('events') or []) if isinstance(ev, dict)]
                locs = [ev['location'] for ev in evs if ev.get('location')]
                label = f'{len(evs)} event' + ('' if len(evs) == 1 else 's')
                if locs:
                    label += ' — ' + ', '.join(dict.fromkeys(locs))
                my_drafts.append({
                    'id': d.id,
                    'permanent': bool(d.permanent),
                    'label': label,
                    'created_at': d.created_at.strftime('%d %b %Y') if d.created_at else '',
                    'source': source_names.get(d.club_id, '') if d.club_id != club.id else '',
                })

    live_ends = ''
    if live_event is not None:
        ee = effective_end(live_event, now)
        live_ends = ee.isoformat() if ee else ''

    return render_template(
        'club_detail.html', club=club, members=members, events=events,
        championships=championships,
        live_event=live_event, live_ends=live_ends,
        is_owner=user_is_owner(club, uname),
        is_admin=is_admin,
        is_member=user_is_member(club, uname),
        has_pending_request=user_has_pending_request(club, uname),
        has_pending_invite=user_has_invite(club, uname),
        pending_users=pending_users,
        pending_invites=pending_invites,
        invite_links=invite_links,
        previous_event=previous_event,
        my_drafts=my_drafts,
        visibility=club_visibility(club),
        join_policy=club_join_policy(club),
    )


@clubs_bp.route('/clubs/<club_id>/edit', methods=['POST'])
@verified_required
def edit_club(club_id: str):
    club = _load_club(club_id)
    if not user_is_admin(club, g.user.username):
        abort(403)
    name = (request.form.get('name') or '').strip()
    desc = (request.form.get('description') or '').strip()
    visibility = (request.form.get('visibility') or 'public').strip().lower()
    join_policy = (request.form.get('join_policy') or 'open').strip().lower()
    if visibility not in ('public', 'private'):
        visibility = 'public'
    if join_policy not in ('open', 'approval'):
        join_policy = 'open'
    if not name:
        flash('Club name is required.', 'error')
        return redirect(url_for('clubs.club_detail', club_id=club_id))
    if len(name) > 40:
        flash('Club name must be under 40 characters.', 'error')
        return redirect(url_for('clubs.club_detail', club_id=club_id))

    club.name = name
    club.description = desc
    # Visibility decides who can find the club at all, so it stays with the
    # owner; an admin's submission leaves it as it was.
    if user_is_owner(club, g.user.username):
        club.visibility = visibility
    club.join_policy = join_policy
    db.session.commit()
    flash('Club updated.', 'success')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


# ── Join / request flow ──────────────────────────────────

@clubs_bp.route('/clubs/<club_id>/join', methods=['POST'])
@verified_required
def join_club(club_id: str):
    club = _load_club(club_id)
    if not club_is_visible_to(club, g.user):
        abort(404)
    uname = g.user.username
    outcome = ''
    if club_join_policy(club) == 'approval' and not user_is_admin(club, uname):
        outcome = enqueue_request(club, uname)
    else:
        if add_member(club, g.user):
            outcome = 'joined'
        else:
            outcome = 'already_member'
    db.session.commit()

    if outcome == 'joined':
        flash(f'Joined {club.name}!', 'success')
    elif outcome == REQ_OK:
        _post_request_notify(club, uname)
        flash(f'Requested to join {club.name}. A club admin will review your request.', 'success')
    elif outcome in (REQ_ALREADY_MEMBER, 'already_member'):
        flash('You are already a member of this club.', 'info')
    elif outcome == REQ_ALREADY_PENDING:
        flash('Your request is already pending approval.', 'info')
    elif outcome == REQ_COOLDOWN:
        flash('Please wait before requesting to join again.', 'warning')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


def _post_request_notify(club: Club, requester_username: str) -> None:
    """Notify + email every reviewer after a request was enqueued. Email only
    fires when add_notification wrote a fresh row, so a request/cancel/request
    loop produces at most one alert per cycle per reviewer."""
    requester = get_user_by_name(requester_username)
    for reviewer_name in club_reviewers(club):
        notif = add_notification(reviewer_name, {
            'type': 'club_join_request',
            'club_id': club.id,
            'from_username': requester_username,
        })
        if notif is None:
            continue
        reviewer = get_user_by_name(reviewer_name)
        if reviewer is None or requester is None:
            continue
        try:
            email_join_request(reviewer, requester, club)
        except Exception:
            db.session.rollback()


@clubs_bp.route('/clubs/<club_id>/request', methods=['POST'])
@verified_required
def request_join_club(club_id: str):
    club = _load_club(club_id)
    if not club_is_visible_to(club, g.user):
        abort(404)
    uname = g.user.username
    if user_is_owner(club, uname):
        flash('You already own this club.', 'info')
        return redirect(url_for('clubs.club_detail', club_id=club_id))
    outcome = enqueue_request(club, uname)
    db.session.commit()
    if outcome == REQ_OK:
        _post_request_notify(club, uname)
        flash(f'Requested to join {club.name}. A club admin will review your request.', 'success')
    elif outcome == REQ_ALREADY_MEMBER:
        flash('You are already a member of this club.', 'info')
    elif outcome == REQ_ALREADY_PENDING:
        flash('Your request is already pending approval.', 'info')
    elif outcome == REQ_COOLDOWN:
        flash('Please wait before requesting to join again.', 'warning')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


@clubs_bp.route('/clubs/<club_id>/request/cancel', methods=['POST'])
@verified_required
def cancel_join_request(club_id: str):
    club = _load_club(club_id)
    uname = g.user.username
    reviewers = club_reviewers(club)
    reqs = pending_requests(club)
    new_pending = [r for r in reqs if r.get('username') != uname]
    canceled = len(new_pending) != len(reqs)
    if canceled:
        set_pending_requests(club, new_pending)
        set_cooldown(club, uname, COOLDOWN_AFTER_CANCEL)
        db.session.commit()
        for reviewer_name in reviewers:
            clear_join_request_notification(reviewer_name, club.id, uname)
        db.session.commit()
        flash('Join request canceled.', 'info')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


@clubs_bp.route('/clubs/<club_id>/requests/<username>/approve', methods=['POST'])
@verified_required
def approve_join_request(club_id: str, username: str):
    if not _SAFE_USER_RE.match(username):
        abort(400)
    club = _require_club_admin(club_id)
    reqs = pending_requests(club)
    if not any(r.get('username') == username for r in reqs):
        flash('No pending request from that user.', 'warning')
        return redirect(url_for('clubs.club_detail', club_id=club_id))
    reviewers = club_reviewers(club)
    set_pending_requests(club, [r for r in reqs if r.get('username') != username])
    clear_cooldown(club, username)
    requester = get_user_by_name(username)
    if requester is not None:
        add_member(club, requester)
    db.session.commit()

    add_notification(username, {'type': 'club_join_approved', 'club_id': club.id})
    if requester is not None:
        try:
            email_join_approved(requester, club)
        except Exception:
            db.session.rollback()
    for reviewer_name in reviewers:
        clear_join_request_notification(reviewer_name, club.id, username)
    db.session.commit()
    flash(f'Approved {username}.', 'success')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


@clubs_bp.route('/clubs/<club_id>/requests/<username>/deny', methods=['POST'])
@verified_required
def deny_join_request(club_id: str, username: str):
    if not _SAFE_USER_RE.match(username):
        abort(400)
    club = _require_club_admin(club_id)
    reqs = pending_requests(club)
    if not any(r.get('username') == username for r in reqs):
        flash('No pending request from that user.', 'warning')
        return redirect(url_for('clubs.club_detail', club_id=club_id))
    reviewers = club_reviewers(club)
    set_pending_requests(club, [r for r in reqs if r.get('username') != username])
    set_cooldown(club, username, COOLDOWN_AFTER_DENY)
    db.session.commit()

    add_notification(username, {'type': 'club_join_denied', 'club_id': club.id})
    requester = get_user_by_name(username)
    if requester is not None:
        try:
            email_join_denied(requester, club)
        except Exception:
            db.session.rollback()
    for reviewer_name in reviewers:
        clear_join_request_notification(reviewer_name, club.id, username)
    db.session.commit()
    flash(f'Denied request from {username}.', 'info')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


@clubs_bp.route('/clubs/<club_id>/leave', methods=['POST'])
@verified_required
def leave_club(club_id: str):
    club = _load_club(club_id)
    uname = g.user.username
    if user_is_owner(club, uname):
        flash("You can't leave a club you own.", 'warning')
        return redirect(url_for('clubs.club_detail', club_id=club_id))
    left = remove_member(club, uname)
    admins = [a for a in json.loads(club.admins_json or '[]') if a != uname]
    if len(admins) != len(json.loads(club.admins_json or '[]')):
        set_club_admins(club, admins)
    db.session.commit()
    if left:
        flash(f'Left {club.name}.', 'info')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


# ── Roles (owner / admin) ────────────────────────────────

@clubs_bp.route('/clubs/<club_id>/admins/<username>/promote', methods=['POST'])
@verified_required
def promote_club_admin(club_id: str, username: str):
    """Only the owner makes a member an admin."""
    if not _SAFE_USER_RE.match(username):
        abort(400)
    club = _load_club(club_id)
    if not user_is_owner(club, g.user.username):
        abort(403)
    if user_is_owner(club, username):
        outcome = 'owner'
    elif not user_is_member(club, username):
        outcome = 'not_member'
    elif username in json.loads(club.admins_json or '[]'):
        outcome = 'already'
    else:
        set_club_admins(club, json.loads(club.admins_json or '[]') + [username])
        db.session.commit()
        add_notification(username, {
            'type': 'club_admin_granted', 'club_id': club.id,
            'club_name': club.name, 'from_username': g.user.username,
        })
        db.session.commit()
        flash(f'{username} is now an admin of {club.name}.', 'success')
        return redirect(url_for('clubs.club_detail', club_id=club_id))

    if outcome == 'owner':
        flash('The club owner is already an admin.', 'info')
    elif outcome == 'already':
        flash(f'{username} is already an admin.', 'info')
    else:
        flash(f'{username} is not a member of this club.', 'warning')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


@clubs_bp.route('/clubs/<club_id>/admins/<username>/demote', methods=['POST'])
@verified_required
def demote_club_admin(club_id: str, username: str):
    """Only the owner removes the admin role."""
    if not _SAFE_USER_RE.match(username):
        abort(400)
    club = _load_club(club_id)
    if not user_is_owner(club, g.user.username):
        abort(403)
    admins = json.loads(club.admins_json or '[]')
    demoted = username in admins
    if demoted:
        set_club_admins(club, [a for a in admins if a != username])
        db.session.commit()
        add_notification(username, {
            'type': 'club_admin_revoked', 'club_id': club.id,
            'club_name': club.name, 'from_username': g.user.username,
        })
        db.session.commit()
        flash(f'{username} is no longer an admin.', 'info')
    else:
        flash(f'{username} is not an admin of this club.', 'warning')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


# ── Invites (direct + shareable link) ────────────────────

@clubs_bp.route('/clubs/<club_id>/invite', methods=['POST'])
@verified_required
def invite_to_club(club_id: str):
    """Admin invites a specific user by username."""
    club = _require_club_admin(club_id)
    invitee_name = (request.form.get('username') or '').strip()
    if not invitee_name:
        flash('Username is required.', 'error')
        return redirect(url_for('clubs.club_detail', club_id=club_id))
    if not _SAFE_USER_RE.match(invitee_name):
        flash('Invalid username.', 'error')
        return redirect(url_for('clubs.club_detail', club_id=club_id))
    invitee = get_user_by_name(invitee_name)
    if invitee is None:
        flash(f'No user named {invitee_name}.', 'error')
        return redirect(url_for('clubs.club_detail', club_id=club_id))

    outcome = ''
    if invitee_name == g.user.username:
        outcome = 'self'
    elif user_is_member(club, invitee_name):
        outcome = 'already_member'
    elif user_has_invite(club, invitee_name):
        outcome = 'already_invited'
    else:
        invs = club_invites(club)
        invs.append({
            'username': invitee_name,
            'invited_by': g.user.username,
            'created_at': utcnow().isoformat(),
        })
        set_club_invites(club, invs)
        # An invite is admin-driven, so any prior cooldown the user had is moot.
        clear_cooldown(club, invitee_name)
        db.session.commit()
        outcome = 'invited'

    if outcome == 'invited':
        notif = add_notification(invitee_name, {
            'type': 'club_invite', 'club_id': club.id,
            'from_username': g.user.username,
        })
        db.session.commit()
        if notif is not None:
            try:
                email_invite(invitee, g.user, club)
            except Exception:
                db.session.rollback()
        flash(f'Invited {invitee_name}.', 'success')
    elif outcome == 'self':
        flash('You cannot invite yourself.', 'warning')
    elif outcome == 'already_member':
        flash(f'{invitee_name} is already a member.', 'info')
    else:
        flash(f'{invitee_name} has already been invited.', 'info')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


@clubs_bp.route('/clubs/<club_id>/invites/<username>/cancel', methods=['POST'])
@verified_required
def cancel_invite(club_id: str, username: str):
    if not _SAFE_USER_RE.match(username):
        abort(400)
    club = _require_club_admin(club_id)
    invs = club_invites(club)
    new_invs = [i for i in invs if i.get('username') != username]
    if len(new_invs) != len(invs):
        set_club_invites(club, new_invs)
        db.session.commit()
        clear_invite_notification(username, club.id)
        db.session.commit()
        flash(f'Invite to {username} canceled.', 'info')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


@clubs_bp.route('/clubs/<club_id>/invite/accept', methods=['POST'])
@verified_required
def accept_invite(club_id: str):
    club = _load_club(club_id)
    uname = g.user.username
    if not user_has_invite(club, uname):
        flash('You do not have a pending invite to this club.', 'warning')
        return redirect(url_for('clubs.club_detail', club_id=club_id))
    set_club_invites(club, [i for i in club_invites(club) if i.get('username') != uname])
    set_pending_requests(club, [r for r in pending_requests(club) if r.get('username') != uname])
    clear_cooldown(club, uname)
    add_member(club, g.user)
    db.session.commit()
    clear_invite_notification(uname, club.id)
    db.session.commit()
    flash(f'Joined {club.name}.', 'success')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


@clubs_bp.route('/clubs/<club_id>/invite/decline', methods=['POST'])
@verified_required
def decline_invite(club_id: str):
    club = _load_club(club_id)
    uname = g.user.username
    invs = club_invites(club)
    new_invs = [i for i in invs if i.get('username') != uname]
    if len(new_invs) != len(invs):
        set_club_invites(club, new_invs)
        db.session.commit()
        clear_invite_notification(uname, club.id)
        db.session.commit()
        flash('Invite declined.', 'info')
    return redirect(url_for('clubs.clubs_list'))


@clubs_bp.route('/clubs/<club_id>/invite-link', methods=['POST'])
@verified_required
def create_invite_link(club_id: str):
    club = _require_club_admin(club_id)
    link = ClubInvite(token=secrets.token_urlsafe(24), club_id=club.id, created_by=g.user.id)
    db.session.add(link)
    db.session.commit()
    flash('Invite link created.', 'success')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


@clubs_bp.route('/clubs/<club_id>/invite-link/<token>/revoke', methods=['POST'])
@verified_required
def revoke_invite_link(club_id: str, token: str):
    """Mark revoked rather than drop the row so the audit history is preserved."""
    club = _require_club_admin(club_id)
    link = ClubInvite.query.filter_by(club_id=club.id, token=token).first()
    if link is not None and link.revoked_at is None:
        link.revoked_at = utcnow()
        db.session.commit()
        flash('Invite link revoked.', 'info')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


@clubs_bp.route('/clubs/<club_id>/join/<token>', methods=['GET', 'POST'])
def join_via_invite_link(club_id: str, token: str):
    """Public landing for a shareable invite link. GET renders the "you've been
    invited" page (works for private clubs); POST accepts and joins."""
    club = _load_club(club_id)
    if find_invite_link(club.id, token) is None:
        abort(404)
    user_id = session.get('user_id')
    user = db.session.get(User, user_id) if user_id else None
    if request.method == 'GET':
        return render_template(
            'club_invite_landing.html', club=club, token=token,
            current_user_obj=user, visibility=club_visibility(club),
        )
    if user is None:
        flash('Please sign in to accept the invite.', 'warning')
        return redirect(url_for('pages.login', next=request.path))
    if not user.email_verified:
        flash('Please verify your email before joining clubs.', 'warning')
        return redirect(url_for('pages.account'))

    joined = add_member(club, user)
    set_pending_requests(club, [r for r in pending_requests(club) if r.get('username') != user.username])
    set_club_invites(club, [i for i in club_invites(club) if i.get('username') != user.username])
    clear_cooldown(club, user.username)
    db.session.commit()
    if joined:
        flash(f'Joined {club.name}.', 'success')
    else:
        flash('You are already a member of this club.', 'info')
    return redirect(url_for('clubs.club_detail', club_id=club_id))


# ── Championship builder (multi-event draft flow) ────────

@clubs_bp.route('/clubs/<club_id>/championship/new', methods=['GET'])
@verified_required
def championship_new(club_id: str):
    club = _require_club_admin(club_id)
    mode = (request.args.get('mode') or CHAMP_MODE_SEASON).strip().lower()
    if mode not in CHAMP_MODES:
        mode = CHAMP_MODE_SEASON
    return render_template('championship_new.html', club=club, mode=mode,
                           scoring=SCORING_WRC, power_stage=False,
                           default_events=3, default_stages=5,
                           max_events=MAX_CHAMP_EVENTS, max_stages=MAX_STAGES_PER_EVENT)


@clubs_bp.route('/clubs/<club_id>/championship/new', methods=['POST'])
@verified_required
def championship_generate(club_id: str):
    club = _require_club_admin(club_id)
    num_events = to_int_or_zero(request.form.get('num_events'))
    num_stages = to_int_or_zero(request.form.get('num_stages'))
    mode = (request.form.get('mode') or CHAMP_MODE_SEASON).strip().lower()
    if mode not in CHAMP_MODES:
        mode = CHAMP_MODE_SEASON
    scoring = (request.form.get('scoring') or SCORING_WRC).strip().lower()
    if scoring not in SCORING_SYSTEMS:
        scoring = SCORING_WRC
    power_stage = request.form.get('power_stage') == '1'
    errors: list[str] = []
    if not (1 <= num_events <= MAX_CHAMP_EVENTS):
        errors.append(f'Number of events must be between 1 and {MAX_CHAMP_EVENTS}.')
    if not (1 <= num_stages <= MAX_STAGES_PER_EVENT):
        errors.append(f'Number of stages must be between 1 and {MAX_STAGES_PER_EVENT}.')
    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('clubs.championship_new', club_id=club_id))

    settings = dict(DEFAULT_CHAMP_SETTINGS)
    settings['scoring'] = scoring
    settings['power_stage'] = power_stage
    draft = {
        'id': f'champ-draft-{secrets.token_hex(4)}',
        'club_id': club.id,
        'owner': g.user.username,
        'mode': mode,
        'start_at': '',
        'settings': settings,
        'events': random_championship_events(num_events, num_stages),
    }
    save_draft(draft)
    return redirect(url_for('clubs.championship_edit', club_id=club.id, draft_id=draft['id']))


@clubs_bp.route('/clubs/<club_id>/championship/<draft_id>', methods=['GET'])
@verified_required
def championship_edit(club_id: str, draft_id: str):
    club = _require_club_admin(club_id)
    draft = _require_draft(club_id, draft_id)
    return render_template('championship_edit.html', **_championship_edit_context(club, draft))


@clubs_bp.route('/clubs/<club_id>/championship/<draft_id>', methods=['POST'])
@verified_required
def championship_action(club_id: str, draft_id: str):
    club = _require_club_admin(club_id)
    draft = _require_draft(club_id, draft_id)

    # Always persist the whole editor form first so nothing is lost on a bounce.
    parsed = parse_championship_form(request.form)
    draft['events'] = parsed['events'] or [blank_event()]

    action = request.form.get('action', 'save')
    anchor = ''
    if action == 'add_event':
        if len(draft['events']) < MAX_CHAMP_EVENTS:
            draft['events'].append(blank_event(1))
            anchor = f'#event-{len(draft["events"]) - 1}'
        else:
            flash(f'A championship can have at most {MAX_CHAMP_EVENTS} events.', 'error')
    elif action.startswith('delete_event:'):
        i = to_int_or_zero(action.split(':', 1)[1])
        if len(draft['events']) > 1 and 0 <= i < len(draft['events']):
            draft['events'].pop(i)
    elif action.startswith('add_stage:'):
        i = to_int_or_zero(action.split(':', 1)[1])
        if 0 <= i < len(draft['events']):
            stages_i = draft['events'][i]['stages']
            if len(stages_i) < MAX_STAGES_PER_EVENT:
                stages_i.append(blank_stage(len(stages_i)))
            else:
                flash(f'An event can have at most {MAX_STAGES_PER_EVENT} stages.', 'error')
            anchor = f'#event-{i}'
    elif action.startswith('delete_stage:'):
        parts = action.split(':')
        i = to_int_or_zero(parts[1]) if len(parts) > 1 else 0
        j = to_int_or_zero(parts[2]) if len(parts) > 2 else 0
        if 0 <= i < len(draft['events']):
            st = draft['events'][i]['stages']
            if len(st) > 1 and 0 <= j < len(st):
                st.pop(j)
            anchor = f'#event-{i}'

    # Multi-Location Rally: only the first event carries a duration (the
    # shared window), so zero out any later-event durations — the editor form
    # doesn't even render them, and a stale value would inflate the total.
    # Likewise only the first event's vehicle class select is rendered; later
    # rallies inherit it (car damage/tyre state carry over between events).
    if draft.get('mode', CHAMP_MODE_MULTI) == CHAMP_MODE_MULTI:
        first_class = draft['events'][0].get('car_class') or ''
        for ev in draft['events'][1:]:
            ev['duration'] = {'days': 0, 'hours': 0, 'mins': 0}
            ev['car_class'] = first_class

    save_draft(draft)
    if action == 'preview':
        return redirect(url_for('clubs.championship_preview', club_id=club.id, draft_id=draft_id))
    return redirect(url_for('clubs.championship_edit', club_id=club.id, draft_id=draft_id) + anchor)


def _offset_label(offset: timedelta) -> str:
    """Offset from the championship start, e.g. 'Day 2' or '36h 30m'."""
    total_h = int(offset.total_seconds() // 3600)
    days, hours = divmod(total_h, 24)
    if days:
        return f'Day {days}' + (f', {hours}h' if hours else '')
    if hours:
        mins = int(offset.total_seconds() % 3600 // 60)
        return f'{hours}h' + (f' {mins:02d}m' if mins else '')
    return f'{int(offset.total_seconds() // 60)}m'


@clubs_bp.route('/clubs/<club_id>/championship/<draft_id>/preview', methods=['GET'])
@verified_required
def championship_preview(club_id: str, draft_id: str):
    club = _require_club_admin(club_id)
    draft = _require_draft(club_id, draft_id)
    mode = draft.get('mode') or CHAMP_MODE_MULTI
    rows = []
    offset = timedelta(0)
    has_rx = False
    for ev in draft.get('events', []):
        d = leg_duration(ev)
        row = {
            'location': ev.get('location') or '(none)',
            'stages': len(ev.get('stages', [])),
            'car_class': ev.get('car_class') or '',
            'duration_label': duration_label(ev.get('duration', {})),
        }
        if ev.get('location') in RX_LOCATIONS:
            has_rx = True
            row['rx'] = True
            laps = ev.get('rx_laps')
            row['laps'] = str(laps) if laps else 'default'
        if mode == CHAMP_MODE_SEASON:
            row['window'] = f'{_offset_label(offset)} → {_offset_label(offset + d)}'
        rows.append(row)
        offset += d
    total = championship_duration(draft.get('events', []))
    now = utcnow()
    busy_until = club_busy_until(club.id, now)
    earliest = max(now, busy_until) if busy_until else now
    start_ref = earliest
    if draft.get('start_at'):
        try:
            start_ref = datetime.fromisoformat(draft['start_at'])
        except ValueError:
            start_ref = earliest
    end_display = ''
    if total.total_seconds() > 0:
        end_display = (start_ref + total).strftime('%a %d %b %Y, %H:%M')
    dsettings = draft.get('settings') or {}
    scoring = dsettings.get('scoring')
    if scoring not in SCORING_SYSTEMS:
        scoring = SCORING_WRC
    return render_template(
        'championship_preview.html', club=club, draft=draft, mode=mode,
        scoring=scoring, power_stage=bool(dsettings.get('power_stage', False)),
        summary_rows=rows, has_rx=has_rx, end_display=end_display,
        total_label=duration_label({
            'days': total.days,
            'hours': total.seconds // 3600,
            'mins': (total.seconds % 3600) // 60,
        }),
        total_seconds=int(total.total_seconds()),
        busy_for=countdown_text(busy_until.isoformat()) if busy_until else '',
        min_iso=earliest.strftime('%Y-%m-%dT%H:%M'),
        min_epoch=int(earliest.timestamp()),
        start_epoch=int(start_ref.timestamp()),
    )


def countdown_text(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str)
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
    except Exception:
        return dt_str


def _parse_start(form) -> tuple[str, datetime | None]:
    """Resolve the picked start instant to a naive-UTC datetime.

    The preview page posts the picked instant as epoch seconds so the viewer's
    timezone survives the trip; fall back to the raw ISO field (treated as UTC)
    when JS is unavailable."""
    start_at = (form.get('start_at') or '').strip()
    start_epoch = (form.get('start_at_epoch') or '').strip()
    if start_epoch:
        try:
            return start_at, datetime.fromtimestamp(float(start_epoch), tz=timezone.utc).replace(tzinfo=None)
        except (ValueError, OSError, OverflowError):
            pass
    if not start_at:
        return '', None
    try:
        dt = datetime.fromisoformat(start_at)
    except ValueError:
        return start_at, None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return start_at, dt


@clubs_bp.route('/clubs/<club_id>/championship/<draft_id>/submit', methods=['POST'])
@verified_required
def championship_submit(club_id: str, draft_id: str):
    club = _require_club_admin(club_id)
    draft = _require_draft(club_id, draft_id)

    mode = (draft.get('mode') or CHAMP_MODE_MULTI).strip().lower()
    if mode not in CHAMP_MODES:
        mode = CHAMP_MODE_MULTI
    draft['mode'] = mode

    # No separate championship name: the club is the identity, so the event
    # carries the club's name (shown on the site and as the in-game challenge).
    name = club.name
    start_at, start_dt_parsed = _parse_start(request.form)
    dsettings = draft.get('settings') or {}
    scoring = dsettings.get('scoring')
    if scoring not in SCORING_SYSTEMS:
        scoring = SCORING_WRC
    settings = {
        'hardcore_damage': request.form.get('adv_hardcore_damage') == '1',
        'unexpected_moments': request.form.get('adv_unexpected_moments') == '1',
        'force_cockpit_camera': request.form.get('adv_force_cockpit') == '1',
        'allow_assists': request.form.get('adv_allow_assists') == '1',
        # Mode travels with the stored event so the game server can tell a
        # shared-window Multi-Location Rally from a staggered Rally Season;
        # scoring/power_stage were picked on the New Championship screen and
        # ride along in the draft settings.
        'mode': mode,
        'scoring': scoring,
        'power_stage': bool(dsettings.get('power_stage', False)),
    }
    draft['start_at'] = start_at
    draft['settings'] = settings
    save_draft(draft)

    events = draft.get('events', [])
    if mode == CHAMP_MODE_MULTI:
        # The shared window is the first event's duration; later events ride
        # on it, so their stored durations must stay zero.  Same for the
        # vehicle class — only the first rally's select exists in the editor.
        first_class = events[0].get('car_class') or '' if events else ''
        for ev in events[1:]:
            ev['duration'] = {'days': 0, 'hours': 0, 'mins': 0}
            ev['car_class'] = first_class
    errors = validate_championship(events, mode=mode)

    now = utcnow()
    start_dt = start_dt_parsed or now
    if start_dt_parsed is None and start_at:
        errors.append('Invalid start date/time.')
    if start_dt < now - timedelta(minutes=5):
        errors.append('Start time cannot be in the past.')
    total = championship_duration(events)
    if total.total_seconds() <= 0:
        errors.append('Total championship duration must be greater than zero.')
    if total > MAX_CHAMP_DURATION:
        errors.append('Total championship duration is too long.')

    # One championship per club at a time: the game has a single event cursor
    # per club, so an overlapping one would be unreachable in game.
    if total.total_seconds() > 0:
        clash = club_event_conflict(club.id, start_dt, start_dt + total)
        if clash:
            errors.append(
                f'"{clash.get("name", "Another event")}" is running in this club '
                f'until {countdown_text(clash.get("end_time") or "")}. The game only '
                'shows one championship per club, so start this one after that.')

    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('clubs.championship_preview', club_id=club.id, draft_id=draft_id))

    end_dt = start_dt + total
    v2_events = []
    for ev in events:
        leg = {
            'location': ev['location'],
            'car_class': ev['car_class'],
            'surface': LOCATION_SURFACE.get(ev['location'], 'Gravel'),
            'duration': ev.get('duration', {}),
            # Rallycross lap option: stored per event; the game server applies
            # it to every stage of the leg. Stripped for rally locations so a
            # stale form value can never leak into the served shape.
            'rx_laps': ev.get('rx_laps') if ev['location'] in RX_LOCATIONS else None,
            'stages': [enrich_stage(ev['location'], s) for s in ev.get('stages', [])],
        }
        v2_events.append(leg)
    first = v2_events[0]
    from ..tables import bucket_for_duration
    event = Event(
        id=f'evt-{secrets.token_hex(4)}',
        schema_version=2,
        name=name,
        type=bucket_for_duration(total),
        club_id=club.id,
        start_time=start_dt, end_time=end_dt,
        active=True, featured=False, system=False,
        settings_json=json.dumps(settings),
        location=first['location'],
        car_class=first['car_class'],
        surface=first['surface'],
        conditions=first['stages'][0]['conditions'] if first['stages'] else '',
        stages_json=json.dumps(first['stages']),
        legs_json=json.dumps(v2_events),
    )
    db.session.add(event)
    # Permanent drafts are reusable templates: they survive submit so the
    # same championship can be recreated from them later.  Regular drafts
    # are one-shot and go away with the created event.
    if not draft.get('permanent'):
        delete_draft(draft_id)
    db.session.commit()
    flash('Championship created!', 'success')
    return redirect(url_for('clubs.club_detail', club_id=club.id))


def _require_club_event(club_id: str, event_id: str) -> dict:
    from ..events import get_event
    event = get_event(event_id)
    if not event or event.get('club_id') != club_id:
        abort(404)
    return event


@clubs_bp.route('/clubs/<club_id>/championship/<event_id>/delete', methods=['GET'])
@verified_required
def championship_delete_confirm(club_id: str, event_id: str):
    club = _require_club_admin(club_id)
    event = _require_club_event(club_id, event_id)
    entries = entries_for_event(event_id)
    drivers = {e.get('username') for e in entries if e.get('username')}
    return render_template('championship_delete.html', club=club, event=event,
                           driver_count=len(drivers),
                           is_live=event_is_active(event))


@clubs_bp.route('/clubs/<club_id>/championship/<event_id>/delete', methods=['POST'])
@verified_required
def championship_delete(club_id: str, event_id: str):
    club = _require_club_admin(club_id)
    event = _require_club_event(club_id, event_id)
    for r in Result.query.filter_by(event_id=event_id).all():
        db.session.delete(r)
    e = db.session.get(Event, event_id)
    if e is not None:
        db.session.delete(e)
    db.session.commit()
    flash(f'Championship "{event.get("name", event_id)}" deleted.', 'info')
    return redirect(url_for('clubs.club_detail', club_id=club.id))


@clubs_bp.route('/clubs/<club_id>/delete', methods=['GET'])
@verified_required
def club_delete_confirm(club_id: str):
    club = _require_club_owner(club_id)
    events = [e for e in get_all_events() if e.get('club_id') == club_id]
    drivers: set[str] = set()
    for e in events:
        for entry in entries_for_event(e['id']):
            if entry.get('username'):
                drivers.add(entry['username'])
    return render_template(
        'club_delete.html', club=club,
        member_count=len(club.members),
        event_count=len(events),
        driver_count=len(drivers),
        is_live=any(event_is_active(e) for e in events),
    )


@clubs_bp.route('/clubs/<club_id>/delete', methods=['POST'])
@verified_required
def club_delete(club_id: str):
    club = _require_club_owner(club_id)
    # Children first: results, events, drafts and invite links point at the
    # club (or its events) with plain FKs; members cascade via Club.members.
    event_ids = [e.id for e in Event.query.filter_by(club_id=club.id).all()]
    if event_ids:
        for r in Result.query.filter(Result.event_id.in_(event_ids)).all():
            db.session.delete(r)
    for e in Event.query.filter_by(club_id=club.id).all():
        db.session.delete(e)
    for d in ChampionshipDraft.query.filter_by(club_id=club.id).all():
        db.session.delete(d)
    for inv in ClubInvite.query.filter_by(club_id=club.id).all():
        db.session.delete(inv)
    db.session.delete(club)
    db.session.commit()
    flash(f'Club "{club.name}" deleted.', 'info')
    return redirect(url_for('clubs.clubs_list'))


@clubs_bp.route('/clubs/<club_id>/championship/<draft_id>/discard', methods=['POST'])
@verified_required
def championship_discard(club_id: str, draft_id: str):
    club = _require_club_admin(club_id)
    _require_draft(club_id, draft_id)
    delete_draft(draft_id)
    flash('Draft discarded.', 'info')
    return redirect(url_for('clubs.club_detail', club_id=club.id))


@clubs_bp.route('/clubs/<club_id>/championship/load-previous', methods=['POST'])
@verified_required
def championship_load_previous(club_id: str):
    """Seed a fresh draft from the club's most recent championship."""
    club = _require_club_admin(club_id)
    prev = _latest_club_event(club.id)
    if prev is None:
        flash('This club has no championship to load.', 'error')
        return redirect(url_for('clubs.club_detail', club_id=club.id))
    draft = draft_from_event(prev, club.id, g.user.username)
    save_draft(draft)
    flash(f'Loaded "{prev.get("name", prev["id"])}" as a new draft.', 'info')
    return redirect(url_for('clubs.championship_edit', club_id=club.id, draft_id=draft['id']))


@clubs_bp.route('/clubs/<club_id>/championship/load-draft', methods=['POST'])
@verified_required
def championship_load_draft(club_id: str):
    """Load one of the user's drafts. A draft of this club is resumed as-is;
    a draft from another club is cloned into this one as a fresh working
    copy (the original stays where it was)."""
    club = _require_club_admin(club_id)
    draft_id = (request.form.get('draft_id') or '').strip()
    d = db.session.get(ChampionshipDraft, draft_id) if draft_id else None
    if d is None or d.owner != g.user.username:
        flash('That draft is not available.', 'error')
        return redirect(url_for('clubs.club_detail', club_id=club.id))
    if d.club_id == club.id:
        return redirect(url_for('clubs.championship_edit', club_id=club.id, draft_id=draft_id))
    clone = dict(get_draft(draft_id))
    clone['id'] = f'champ-draft-{secrets.token_hex(4)}'
    clone['club_id'] = club.id
    clone['permanent'] = False
    save_draft(clone)
    src = db.session.get(Club, d.club_id)
    flash(f'Loaded a draft from "{src.name if src else d.club_id}" as a new draft.', 'info')
    return redirect(url_for('clubs.championship_edit', club_id=club.id, draft_id=clone['id']))


@clubs_bp.route('/clubs/<club_id>/championship/<draft_id>/save-permanent', methods=['POST'])
@verified_required
def championship_save_permanent(club_id: str, draft_id: str):
    """Mark a draft permanent so it survives submit and can be reloaded."""
    club = _require_club_admin(club_id)
    _require_draft(club_id, draft_id)
    d = db.session.get(ChampionshipDraft, draft_id)
    d.permanent = True
    db.session.commit()
    flash('Draft saved as permanent — it stays available to reload after submit.', 'success')
    return redirect(url_for('clubs.championship_preview', club_id=club.id, draft_id=draft_id))
