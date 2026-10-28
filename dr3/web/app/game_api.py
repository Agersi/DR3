"""Game-facing REST API (called by each player's local dr2server).

Exact contract of the club-site game API (see docs/notes/web_server_api.md):
Bearer ``df_`` token auth, JSON in/out. Web-page routes live in ``pages``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, redirect, request, session, url_for

from . import csrf
from .auth import (
    cron_auth_required,
    game_auth_required,
    issue_game_token,
    login_required,
)
from .db import db
from .events import (
    entries_for_event,
    entry_nationality,
    event_is_active,
    event_is_upcoming,
    get_all_events,
    get_event,
    global_stage_index,
    sort_events,
    upsert_result,
    validate_id,
)
from .models import Club, ClubMember, GameToken, User, utcnow
from .scoring import car_label, championship_points, overall_rank_key
from .tables import normalize_championship

game_api_bp = Blueprint('game_api', __name__)


def _api_error(message: str, status: int = 400):
    return jsonify({'ok': False, 'error': message}), status


def _stable_int_id(string_id: str, base: int = 100000) -> int:
    """Derive a stable positive integer from a string (md5-based)."""
    h = int.from_bytes(hashlib.md5(string_id.encode()).digest()[:4], 'little')
    return base + (h % 90000)


def _client_version_file() -> Path:
    """VERSION.txt next to the served client zip (dr3/dist/)."""
    return Path(__file__).resolve().parents[2] / 'dist' / 'VERSION.txt'


@game_api_bp.route('/api/client-version')
def api_client_version():
    """Latest Windows client version + download link. Public: the client's
    update banner polls this before login."""
    try:
        version = _client_version_file().read_text(encoding='utf-8').strip()
    except OSError:
        return _api_error('no client build published', 404)
    if not version:
        return _api_error('no client build published', 404)
    site = current_app.config['SITE_URL'].rstrip('/')
    return jsonify({
        'ok': True,
        'version': version,
        'download_url': f'{site}/download/DR3-windows.zip',
    })


def _club_dict(club: Club, user_id: int) -> dict:
    member = next((m for m in club.members if m.user_id == user_id), None)
    return {
        'id': club.id,
        'name': club.name,
        'description': club.description or '',
        'visibility': club.visibility,
        'join_policy': club.join_policy,
        # Reference contract: the creator's username (string) — it feeds the
        # game's CreatorName badge. The ORM column is a numeric FK instead.
        'created_by': club.owner.username if club.owner else '',
        'members': [
            {'user_id': m.user_id,
             'username': m.user.username if m.user else '',
             'role': m.role}
            for m in club.members
        ],
        'is_member': member is not None,
        'role': member.role if member else '',
    }


def _user_club_ids(user_id: int) -> list[str]:
    return [m.club_id for m in ClubMember.query.filter_by(user_id=user_id).all()]


def _is_club_member(club_id: str, user_id: int) -> bool:
    return (ClubMember.query
            .filter_by(club_id=club_id, user_id=user_id)
            .first() is not None)


def _soonest_event_ends(club_ids: set[str], now: datetime) -> dict[str, datetime]:
    """Per club, the end time of its soonest-expiring live/upcoming championship.

    Mirrors the /clubs page ordering so the in-game clubs screen matches the
    website (bug #15). Live seasons count by their current leg's window end,
    not the last leg's (effective_end)."""
    from .results_view import effective_end

    best: dict[str, datetime] = {}
    for e in get_all_events():
        cid = e.get('club_id')
        if not cid or cid not in club_ids:
            continue
        if not (event_is_active(e, now) or event_is_upcoming(e, now)):
            continue
        end = effective_end(e, now)
        if end is None:
            continue
        cur = best.get(cid)
        if cur is None or end < cur:
            best[cid] = end
    return best


# ── Web-session token management ─────────────────────────

@game_api_bp.route('/api/token/generate', methods=['POST'])
@login_required
def api_token_generate():
    for gt in g.user.tokens:
        if gt.revoked_at is None:
            gt.revoked_at = utcnow()
    gt = issue_game_token(g.user)
    session['new_game_token'] = gt.token
    return redirect(url_for('pages.dashboard'))


@game_api_bp.route('/api/token/revoke', methods=['POST'])
@login_required
def api_token_revoke():
    from flask import flash
    for gt in g.user.tokens:
        if gt.revoked_at is None:
            gt.revoked_at = utcnow()
    db.session.commit()
    flash('Game token revoked.', 'info')
    return redirect(url_for('pages.dashboard'))


# ── Game API (Bearer df_ token) ──────────────────────────

@game_api_bp.route('/api/game/token-test')
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_token_test():
    return jsonify({'ok': True, 'username': g.game_user.username})


@game_api_bp.route('/api/game/clubs')
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_clubs():
    """The caller's clubs and their active events. Only clubs the user is a
    member of are returned, so private clubs never leak to the game client."""
    user = g.game_user
    my_club_ids = _user_club_ids(user.id)
    club_objs = [
        db.session.get(Club, cid)
        for cid in dict.fromkeys(my_club_ids)
    ]
    club_objs = [c for c in club_objs if c is not None]
    # Soonest-expiring live/upcoming championship first; clubs with none last,
    # newest-created first within each group (mirrors the /clubs page order).
    now = utcnow()
    soonest = _soonest_event_ends({c.id for c in club_objs}, now)

    def sort_key(c: Club):
        created = c.created_at.timestamp() if c.created_at else 0.0
        end = soonest.get(c.id)
        if end is None:
            return (1, 0.0, -created)
        return (0, end.timestamp(), -created)

    club_objs.sort(key=sort_key)
    clubs = [_club_dict(c, user.id) for c in club_objs]
    events = [
        normalize_championship(e)
        for e in sort_events(get_all_events())
        if event_is_active(e) and e.get('club_id') in set(my_club_ids)
    ]
    return jsonify({'ok': True, 'clubs': clubs, 'events': events})


@game_api_bp.route('/api/game/profile')
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_profile():
    user = g.game_user
    return jsonify({
        'ok': True,
        'username': user.username,
        'display_name': user.display_name or user.username,
        'country': user.country or '',
        'soft_currency': user.soft_currency if user.soft_currency is not None else 500000,
        'hard_currency': user.hard_currency or 0,
        'garage_slots': user.garage_slots if user.garage_slots is not None else 8,
        # Per-vehicle-instance tuning/livery keyed by VehicleInstId (string)
        # -> {tuning_id, livery_id}; read back into RaceNetInventory so fitted
        # setup survives a restart.
        'garage': json.loads(user.garage_json or '{}'),
    })


@game_api_bp.route('/api/game/garage', methods=['POST'])
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_garage_set():
    """Persist a tuning/livery selection. Only fields present are updated;
    transient in-event instance ids (<= 0) are acknowledged but not stored."""
    data = request.get_json(silent=True) or {}
    user = g.game_user

    inst = data.get('vehicle_inst_id')
    if inst is None:
        return _api_error('vehicle_inst_id is required')
    try:
        inst_int = int(inst)
    except (TypeError, ValueError):
        return _api_error('invalid vehicle_inst_id')

    if inst_int <= 0:
        return jsonify({'ok': True, 'stored': False})

    garage = json.loads(user.garage_json or '{}')
    entry = garage.setdefault(str(inst_int), {})
    if data.get('tuning_id') is not None:
        entry['tuning_id'] = int(data['tuning_id'])
    if data.get('livery_id') is not None:
        entry['livery_id'] = int(data['livery_id'])
    user.garage_json = json.dumps(garage)
    db.session.commit()
    return jsonify({'ok': True, 'stored': True, 'garage': garage})


@game_api_bp.route('/api/game/stage-begin', methods=['POST'])
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_stage_begin():
    """Store pre-stage setup data (tuning, tyres, livery) before a stage starts."""
    data = request.get_json(silent=True) or {}
    user = g.game_user
    event_id = str(data.get('event_id', '')).strip()

    if not event_id:
        return _api_error('event_id is required')
    try:
        validate_id(event_id)
    except ValueError:
        return _api_error('invalid event_id')

    event = get_event(event_id)
    if not event:
        return _api_error('event not found', 404)

    event_club_id = event.get('club_id')
    if event_club_id and not _is_club_member(event_club_id, user.id):
        return _api_error('not a member of this club', 403)

    stage_index = int(data.get('stage_index', 0))
    event_index = int(data.get('event_index', 0) or 0)
    pos = global_stage_index(event, event_index, stage_index)

    r = upsert_result(event_id, user.username)
    in_progress = json.loads(r.in_progress_json or '{}')
    in_progress[str(pos)] = {
        'vehicle_id': data.get('vehicle_id'),
        'livery_id': data.get('livery_id'),
        'tuning_setup_b64': data.get('tuning_setup_b64', ''),
        'tyre_compound': data.get('tyre_compound', 2),
        'tyres_remaining': data.get('tyres_remaining', 3),
        'nationality_id': data.get('nationality_id', 0),
    }
    r.in_progress_json = json.dumps(in_progress)
    db.session.commit()
    return jsonify({'ok': True})


@game_api_bp.route('/api/game/stage-complete', methods=['POST'])
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_stage_complete():
    """Accept a completed stage submission. Partial submissions (e.g. the
    leaderboard pre-persist that only knows time_ms) never clobber stored
    fields — missing keys preserve prior values."""
    data = request.get_json(silent=True) or {}
    user = g.game_user
    event_id = str(data.get('event_id', '')).strip()

    if not event_id:
        return _api_error('event_id is required')
    try:
        validate_id(event_id)
    except ValueError:
        return _api_error('invalid event_id')

    event = get_event(event_id)
    if not event:
        return _api_error('event not found', 404)

    event_club_id = event.get('club_id')
    if event_club_id and not _is_club_member(event_club_id, user.id):
        return _api_error('not a member of this club', 403)

    stage_index = int(data.get('stage_index', 0))
    event_index = int(data.get('event_index', 0) or 0)
    pos = global_stage_index(event, event_index, stage_index)
    time_ms = int(data.get('time_ms', 0))
    vehicle_id = data.get('vehicle_id')
    mark_dnf = bool(data.get('mark_dnf'))

    r = upsert_result(event_id, user.username)
    stages = json.loads(r.stages_json or '[]')
    in_progress_all = json.loads(r.in_progress_json or '{}')
    in_progress_entry = in_progress_all.get(str(pos), {}) or {}

    while len(stages) <= pos:
        stages.append({'time_ms': 0, 'penalties_ms': 0, 'submitted_at': None})
    prev_stage_entry = stages[pos] or {}

    # Routing/identity fields that don't belong inside a stage entry.
    _routing_keys = {'event_id', 'username', 'stage_index', 'event_index'}

    if mark_dnf:
        # DNF close-out marker (backlog 32): the game pre-persisted the stage's
        # time limit via its post-wreck leaderboard query, so keep the stored
        # time and only flag the entry as a non-finish. Never burns an attempt
        # — a mid-leg DNF must not lock out the rest of the leg.
        stage_entry = {
            **prev_stage_entry,
            **{k: v for k, v in in_progress_entry.items() if v is not None},
        }
        stage_entry['race_status'] = int(data.get('race_status') or 4)
        stage_entry.setdefault('time_ms', 0)
        stage_entry.setdefault('penalties_ms', 0)
        vid = stage_entry.get('vehicle_id')
    else:
        # Merge oldest→newest so newer wins: prior submission, then stage-begin
        # capture, then this submission's explicitly-sent fields.
        stage_entry = {
            **prev_stage_entry,
            **{k: v for k, v in in_progress_entry.items() if v is not None},
            **{k: v for k, v in data.items()
               if v is not None and k not in _routing_keys},
        }
        stage_entry['time_ms'] = time_ms
        stage_entry['submitted_at'] = utcnow().isoformat()
        if vehicle_id is not None:
            stage_entry['vehicle_id'] = vehicle_id
        stage_entry.setdefault('penalties_ms', 0)
        stage_entry.setdefault('race_status', 0)
        vid = vehicle_id if vehicle_id is not None else stage_entry.get('vehicle_id')

    stages[pos] = stage_entry

    # A DNF/retired finish burns an attempt; race_status==0 is a clean finish.
    # Markers don't count — see the mark_dnf branch above.
    if not mark_dnf and stage_entry['race_status'] != 0:
        r.attempts_used = (r.attempts_used or 0) + 1

    r.total_time_ms = sum(
        s['time_ms'] + s.get('penalties_ms', 0)
        for s in stages
        if s.get('time_ms', 0) > 0
    )
    # The Result-level vehicle (leaderboards read this column, not the stage
    # entries): a pre-persisted row has no submitted vehicle of its own, so
    # fall back to whatever the stage entry carries from the stage-begin.
    if vid:
        r.vehicle_id = vid
        r.car = car_label({'vehicle_id': vid})
    r.stages_json = json.dumps(stages)
    db.session.commit()

    entries = entries_for_event(event_id)
    entries.sort(key=overall_rank_key(event))
    position = next(
        (i + 1 for i, e in enumerate(entries) if e['username'] == user.username), 0
    )
    return jsonify({'ok': True, 'position': position, 'total_entries': len(entries)})


@game_api_bp.route('/api/game/my-progress')
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_my_progress():
    """The caller's full stage progress across all events."""
    username = g.game_user.username
    events_out = []
    for evt in get_all_events():
        evt_id = evt.get('id', '')
        if not evt_id:
            continue
        user_entry = next(
            (e for e in entries_for_event(evt_id) if e.get('username') == username),
            None,
        )
        if not user_entry:
            continue

        completed = []
        for i, s in enumerate(user_entry.get('stages', [])):
            if not s or s.get('time_ms', 0) <= 0:
                continue
            completed.append({
                'stage_index': i,
                'time_ms': s.get('time_ms', 0),
                'penalties_ms': s.get('penalties_ms', 0),
                'meters_driven': s.get('meters_driven', 0),
                'distance_driven': s.get('distance_driven', 0),
                'vehicle_id': s.get('vehicle_id') or user_entry.get('vehicle_id'),
                'livery_id': s.get('livery_id', 0),
                'nationality_id': s.get('nationality_id', 0),
                'has_repaired': s.get('has_repaired', False),
                'repair_penalty_ms': s.get('repair_penalty_ms', 0),
                'tuning_setup_b64': s.get('tuning_setup_b64', ''),
                'tyre_compound': s.get('tyre_compound', 2),
                'tyres_remaining': s.get('tyres_remaining', 3),
                'vehicle_damage': s.get('comp_damage') or {},
                'vehicle_mud': s.get('vehicle_mud') or {},
            })

        events_out.append({
            'event_id': evt_id,
            'completed_stages': completed,
            'total_time_ms': user_entry.get('total_time_ms', 0),
            'attempts_used': user_entry.get('attempts_used', 0),
        })

    return jsonify({'ok': True, 'events': events_out})


@game_api_bp.route('/api/game/leaderboard/<event_id>')
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_leaderboard(event_id: str):
    try:
        validate_id(event_id)
    except ValueError:
        return _api_error('invalid event_id')

    entries = entries_for_event(event_id)
    event = get_event(event_id)
    if event:
        # Never trust stored order — always re-rank bogey-adjusted.
        entries = sorted(entries, key=overall_rank_key(event))
    points = championship_points(event, entries) if event else {}
    out = []
    for i, e in enumerate(entries):
        out.append({
            'rank': i + 1,
            'username': e['username'],
            'car': e.get('car', ''),
            'vehicle_id': e.get('vehicle_id'),
            'total_time_ms': e['total_time_ms'],
            'points': points.get(e['username'], 0),
            'nationality_id': entry_nationality(e),
            'stages': e.get('stages', []),
        })
    return jsonify({'ok': True, 'entries': out, 'total': len(out)})


@game_api_bp.route('/api/game/events/<event_id>')
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_event(event_id: str):
    try:
        validate_id(event_id)
    except ValueError:
        return _api_error('invalid event_id')

    event = get_event(event_id)
    if not event:
        return _api_error('event not found', 404)
    return jsonify({'ok': True, 'event': event})


@game_api_bp.route('/api/game/auth', methods=['POST'])
@csrf.exempt  # type: ignore[untyped-decorator]
def api_game_auth():
    """Link a Steam account to a web account (display_name/username match)."""
    data = request.get_json(silent=True) or {}
    steam_name = str(data.get('steam_name', '')).strip()
    account_id = data.get('account_id')

    if not steam_name:
        return _api_error('steam_name is required')

    needle = steam_name.lower()
    user = next(
        (u for u in User.query.all()
         if (u.display_name or '').lower() == needle
         or u.username.lower() == needle),
        None,
    )

    if user:
        if account_id is not None and user.steam_account_id != account_id:
            user.steam_account_id = account_id
            db.session.commit()
        return jsonify({
            'ok': True,
            'linked': True,
            'username': user.username,
            'display_name': user.display_name,
        })

    return jsonify({'ok': True, 'linked': False, 'steam_name': steam_name})


# ── Cron ─────────────────────────────────────────────────

@game_api_bp.route('/api/cron', methods=['POST'])
@csrf.exempt  # type: ignore[untyped-decorator]
@cron_auth_required
def api_cron():
    from .events_generator import run_cron_tick
    result = run_cron_tick(utcnow())
    return jsonify({'ok': True, **result})
