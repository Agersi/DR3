"""Club domain helpers on the ORM.

Clubs store their membership in ``club_members`` rows; roles/requests/invites
live in JSON columns mirroring the game's club document shape.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta

from flask import current_app

from .auth import send_email
from .db import db
from .models import (
    ChampionshipDraft,
    Club,
    ClubInvite,
    ClubMember,
    Event,
    Notification,
    User,
    utcnow,
)

log = logging.getLogger(__name__)

MAX_NOTIFICATIONS = 200

# Cooldown windows for re-requesting club membership after a self-cancel or
# owner-deny. Keeps the request/cancel/request loop from spamming the owner.
COOLDOWN_AFTER_CANCEL = timedelta(minutes=10)
COOLDOWN_AFTER_DENY = timedelta(hours=1)


# ── JSON column accessors ────────────────────────────────

def _load_list(club: Club, field: str) -> list:
    try:
        v = json.loads(getattr(club, field) or '[]')
    except (TypeError, ValueError):
        return []
    return v if isinstance(v, list) else []


def club_admins(club: Club) -> list[str]:
    return [x for x in _load_list(club, 'admins_json') if isinstance(x, str)]


def set_club_admins(club: Club, names: list[str]) -> None:
    club.admins_json = json.dumps(list(names))


def pending_requests(club: Club) -> list[dict]:
    return _load_list(club, 'pending_requests_json')


def set_pending_requests(club: Club, reqs: list[dict]) -> None:
    club.pending_requests_json = json.dumps(reqs)


def club_invites(club: Club) -> list[dict]:
    return _load_list(club, 'invites_json')


def set_club_invites(club: Club, invs: list[dict]) -> None:
    club.invites_json = json.dumps(invs)


def club_cooldowns(club: Club) -> dict[str, str]:
    try:
        v = json.loads(club.cooldowns_json or '{}')
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def set_club_cooldowns(club: Club, cd: dict) -> None:
    club.cooldowns_json = json.dumps(cd)


# ── Roles & visibility ───────────────────────────────────

def get_user_by_name(username: str | None) -> User | None:
    if not username:
        return None
    return User.query.filter_by(username=username).first()


def club_owner_username(club: Club) -> str | None:
    return club.owner.username if club.owner else None


def club_visibility(club: Club) -> str:
    return club.visibility or 'public'


def club_join_policy(club: Club) -> str:
    return club.join_policy or 'open'


def user_is_member(club: Club, username: str | None) -> bool:
    u = get_user_by_name(username)
    if u is None:
        return False
    return (ClubMember.query
            .filter_by(club_id=club.id, user_id=u.id)
            .first() is not None)


def user_is_owner(club: Club, username: str | None) -> bool:
    return bool(username) and club_owner_username(club) == username


def user_is_admin(club: Club, username: str | None) -> bool:
    """Owner or promoted admin. Admins manage members and run championships."""
    if not username:
        return False
    return user_is_owner(club, username) or username in club_admins(club)


def club_reviewers(club: Club) -> list[str]:
    """Owner first, then admins: everyone who reviews join requests."""
    names: list[str] = []
    for n in [club_owner_username(club) or ''] + club_admins(club):
        if n and n not in names:
            names.append(n)
    return names


def club_role(club: Club, username: str | None) -> str | None:
    if user_is_owner(club, username):
        return 'owner'
    if user_is_admin(club, username):
        return 'admin'
    if user_is_member(club, username):
        return 'member'
    return None


def user_has_invite(club: Club, username: str | None) -> bool:
    if not username:
        return False
    return any(i.get('username') == username for i in club_invites(club))


def user_has_pending_request(club: Club, username: str | None) -> bool:
    if not username:
        return False
    return any(r.get('username') == username for r in pending_requests(club))


def club_is_visible_to(club: Club, user: User | None) -> bool:
    if club_visibility(club) == 'public':
        return True
    uname = user.username if user else None
    return (
        user_is_owner(club, uname)
        or user_is_member(club, uname)
        or user_has_invite(club, uname)
    )


def find_invite_link(club_id: str, token: str) -> ClubInvite | None:
    if not token:
        return None
    return (ClubInvite.query
            .filter_by(club_id=club_id, token=token)
            .filter(ClubInvite.revoked_at.is_(None))
            .first())


# ── Membership mutation ──────────────────────────────────

def add_member(club: Club, user: User) -> bool:
    existing = (ClubMember.query
                .filter_by(club_id=club.id, user_id=user.id)
                .first())
    if existing is not None:
        return False
    db.session.add(ClubMember(club_id=club.id, user_id=user.id, role='member'))
    return True


def remove_member(club: Club, username: str) -> bool:
    u = get_user_by_name(username)
    if u is None:
        return False
    m = (ClubMember.query
         .filter_by(club_id=club.id, user_id=u.id)
         .first())
    if m is None:
        return False
    db.session.delete(m)
    return True


def member_usernames(club: Club) -> list[str]:
    out = []
    for m in club.members:
        if m.user is not None:
            out.append(m.user.username)
    return out


# ── Cooldowns & request queue ────────────────────────────

def _now_iso() -> str:
    return utcnow().isoformat()


def cooldown_remaining(club: Club, username: str) -> timedelta | None:
    raw = club_cooldowns(club).get(username)
    if not raw:
        return None
    try:
        until = datetime.fromisoformat(raw)
    except ValueError:
        return None
    delta = until - utcnow()
    return delta if delta.total_seconds() > 0 else None


def set_cooldown(club: Club, username: str, window: timedelta) -> None:
    cd = club_cooldowns(club)
    cd[username] = (utcnow() + window).isoformat()
    set_club_cooldowns(club, cd)


def clear_cooldown(club: Club, username: str) -> None:
    cd = club_cooldowns(club)
    if username in cd:
        del cd[username]
        set_club_cooldowns(club, cd)


REQ_OK = 'requested'
REQ_ALREADY_MEMBER = 'already_member'
REQ_ALREADY_PENDING = 'already_pending'
REQ_COOLDOWN = 'cooldown'


def enqueue_request(club: Club, username: str) -> str:
    """Add a pending request to the club. Returns one of the REQ_* sentinels."""
    if user_is_member(club, username):
        return REQ_ALREADY_MEMBER
    reqs = pending_requests(club)
    if any(r.get('username') == username for r in reqs):
        return REQ_ALREADY_PENDING
    if cooldown_remaining(club, username):
        return REQ_COOLDOWN
    reqs.append({'username': username, 'requested_at': _now_iso()})
    set_pending_requests(club, reqs)
    return REQ_OK


# ── Event scheduling constraints ─────────────────────────

def club_event_conflict(club_id: str, start: datetime, end: datetime) -> dict | None:
    """Return a club event whose window overlaps ``[start, end)``, if any.

    A club carries a single event cursor in the game and only the current one
    is served, so two championships live at once leaves one unreachable.
    Back-to-back windows are fine; only real overlaps are rejected.
    """
    for e in Event.query.filter_by(club_id=club_id, active=True).all():
        if e.start_time and e.end_time and e.start_time < end and start < e.end_time:
            return e.to_dict()
    return None


def club_busy_until(club_id: str, now: datetime | None = None) -> datetime | None:
    """Latest end time across the club's events that haven't finished yet."""
    now = now or utcnow()
    ends = [
        e.end_time for e in Event.query.filter_by(club_id=club_id, active=True).all()
        if e.end_time is not None and e.end_time > now
    ]
    return max(ends) if ends else None


# ── Championship drafts ──────────────────────────────────

def get_draft(draft_id: str) -> dict | None:
    d = db.session.get(ChampionshipDraft, draft_id)
    if d is None:
        return None
    try:
        data = json.loads(d.data_json or '{}')
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data['id'] = d.id
    data['club_id'] = d.club_id
    data['owner'] = d.owner
    data['created_at'] = d.created_at.isoformat() if d.created_at else ''
    return data


def save_draft(draft: dict) -> None:
    did = draft.get('id') or f'champ-draft-{secrets.token_hex(4)}'
    d = db.session.get(ChampionshipDraft, did)
    if d is None:
        d = ChampionshipDraft(
            id=did,
            club_id=draft.get('club_id', ''),
            owner=draft.get('owner', ''),
        )
        db.session.add(d)
    payload = {k: v for k, v in draft.items()
               if k not in ('id', 'club_id', 'owner', 'created_at')}
    d.data_json = json.dumps(payload)
    db.session.commit()


def delete_draft(draft_id: str) -> None:
    d = db.session.get(ChampionshipDraft, draft_id)
    if d is not None:
        db.session.delete(d)
        db.session.commit()


# ── Notifications ────────────────────────────────────────

def add_notification(username: str, payload: dict) -> Notification | None:
    """Append a notification for the user.

    Returns the new row, or None when suppressed: for ``club_join_request`` an
    existing unread alert from the same requester for the same club de-dupes
    re-adds, so a request/cancel/request loop produces at most one alert per
    reviewer per cycle.
    """
    user = get_user_by_name(username)
    if user is None:
        return None
    kind = str(payload.get('type') or '')
    body = {k: v for k, v in payload.items() if k != 'type'}
    if kind == 'club_join_request':
        rows = (Notification.query
                .filter_by(user_id=user.id, kind=kind, read=False)
                .all())
        for n in rows:
            try:
                p = json.loads(n.payload_json or '{}')
            except (TypeError, ValueError):
                continue
            if (p.get('club_id') == payload.get('club_id')
                    and p.get('from_username') == payload.get('from_username')):
                return None
    n = Notification(user_id=user.id, kind=kind, payload_json=json.dumps(body))
    db.session.add(n)
    db.session.flush()
    _trim_notifications(user.id)
    return n


def _trim_notifications(user_id: int) -> None:
    rows = (Notification.query
            .filter_by(user_id=user_id)
            .order_by(Notification.created_at.asc(), Notification.id.asc())
            .all())
    excess = len(rows) - MAX_NOTIFICATIONS
    if excess <= 0:
        return
    for n in rows:
        if excess <= 0:
            break
        if n.read:
            db.session.delete(n)
            excess -= 1
    if excess > 0:
        remaining = (Notification.query
                     .filter_by(user_id=user_id, read=False)
                     .order_by(Notification.created_at.asc(), Notification.id.asc())
                     .all())
        for n in remaining[:excess]:
            db.session.delete(n)


def clear_join_request_notification(owner_username: str, club_id: str,
                                    requester_username: str) -> None:
    """Drop an owner's join_request alerts when the request is resolved."""
    user = get_user_by_name(owner_username)
    if user is None:
        return
    for n in Notification.query.filter_by(user_id=user.id, kind='club_join_request').all():
        try:
            p = json.loads(n.payload_json or '{}')
        except (TypeError, ValueError):
            continue
        if (p.get('club_id') == club_id
                and p.get('from_username') == requester_username):
            db.session.delete(n)


def clear_invite_notification(invitee_username: str, club_id: str) -> None:
    """Drop the invitee's club_invite alerts when the invite is no longer pending."""
    user = get_user_by_name(invitee_username)
    if user is None:
        return
    for n in Notification.query.filter_by(user_id=user.id, kind='club_invite').all():
        try:
            p = json.loads(n.payload_json or '{}')
        except (TypeError, ValueError):
            continue
        if p.get('club_id') == club_id:
            db.session.delete(n)


def unread_count(user: User | None) -> int:
    if not user:
        return 0
    return Notification.query.filter_by(user_id=user.id, read=False).count()


# ── Email (best-effort; send_email swallows failures) ────

def _site_url() -> str:
    return current_app.config['SITE_URL'].rstrip('/')


def email_join_request(reviewer: User, requester: User, club: Club) -> bool:
    if not reviewer.email:
        return False
    link = f'{_site_url()}/clubs/{club.id}'
    body = (
        f'Hi {reviewer.display_name or reviewer.username},\n\n'
        f'{requester.display_name or requester.username} has requested '
        f'to join your club "{club.name}".\n\n'
        f'Review and approve or deny the request here:\n{link}\n\n'
        f'- Dirt Rally 3.0'
    )
    return send_email(reviewer.email, f'Join request for {club.name}', body)


def email_join_approved(user: User, club: Club) -> bool:
    if not user.email:
        return False
    link = f'{_site_url()}/clubs/{club.id}'
    body = (
        f'Hi {user.display_name or user.username},\n\n'
        f'Your request to join "{club.name}" was approved. You can now '
        f'submit times to its events.\n\n{link}\n\n- Dirt Rally 3.0'
    )
    return send_email(user.email, f'You joined {club.name}', body)


def email_join_denied(user: User, club: Club) -> bool:
    if not user.email:
        return False
    body = (
        f'Hi {user.display_name or user.username},\n\n'
        f'Your request to join "{club.name}" was declined.\n\n'
        f'- Dirt Rally 3.0'
    )
    return send_email(user.email, f'Join request for {club.name} declined', body)


def email_invite(invitee: User, inviter: User, club: Club) -> bool:
    if not invitee.email:
        return False
    link = f'{_site_url()}/clubs/{club.id}'
    body = (
        f'Hi {invitee.display_name or invitee.username},\n\n'
        f'{inviter.display_name or inviter.username} invited you to '
        f'join their club "{club.name}" on Dirt Rally 3.0.\n\n'
        f'View the club and accept here:\n{link}\n\n'
        f'- Dirt Rally 3.0'
    )
    return send_email(invitee.email, f'Invitation to join {club.name}', body)
