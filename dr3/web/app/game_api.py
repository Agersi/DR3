"""Game-facing REST API (called by each player's local dr2server).

Exact contract of the club-site game API (see docs/notes/web_server_api.md):
Bearer ``dr3_`` token auth (legacy ``df_`` tokens remain valid), JSON in/out.
Web-page routes live in ``pages``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
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
    upsert_stage_state,
    validate_id,
)
from .models import Club, ClubMember, GameToken, StageRun, TimeTrialEntry, User, utcnow
from .scoring import car_label, championship_points, overall_rank_key
from .tables import RX_LOCATIONS, normalize_championship
from dr2server.game_data import Track

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


# ── Game API (Bearer dr3_ token; legacy df_ tokens valid) ──

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
        'tyre_compound': data.get('tyre_compound', 2),
        'tyres_remaining': data.get('tyres_remaining', 3),
        'nationality_id': data.get('nationality_id', 0),
        # Parsed from the tuning blob by the game server (float at offset 210);
        # None on older clients that don't send it.
        'spare_tyres': data.get('spare_tyres'),
    }
    r.in_progress_json = json.dumps(in_progress)
    # The tuning blob lives in club_stage_states — it used to be duplicated
    # into both JSON columns, which grew unbounded over a long championship.
    upsert_stage_state(event_id, user.username, pos,
                       tuning_b64=data.get('tuning_setup_b64', ''))
    db.session.commit()
    return jsonify({'ok': True})


# The snapshot's stage_time_seconds and PostTime's StageTime are the same game
# clock read as two different floats (one rounded to ms, one truncated), so the
# two channels' integer milliseconds can differ by a hair. Tolerance for
# "same run" matching across channels.
_BEST_TOL_MS = 1000

# One run's two channels file seconds apart — a TT's snapshot-only half waits
# out the client's 45 s flush — so the same-run follow-up window is generous;
# a second attempt at the same stage can't file within it (re-entry plus
# another full run takes minutes). Matches the retransmit-collapse window in
# api_game_stage_detail.
_SAME_RUN_WINDOW_S = 120


def _upsert_best(username, vehicle_id, track, conditions, time_ms, *,
                 category=None, vclass=None, livery_id=None,
                 using_wheel=None, using_assists=None,
                 ghost_b64=None, splits=None, tyre_compound=None,
                 run_id=None) -> None:
    """Update the driver's personal best (best_times) for car + track +
    conditions + tyre compound. Every run type posts here — club/official/TT
    PostTimes (with the run's ghost, no compound) and clean stage-detail
    finishes (splits + the compound from the StageEnded snapshot, no ghost).

    The compound is only known once the snapshot arrives, so a run lands in two
    steps: its PostTime files a compound=0 placeholder row, and the stage-detail
    follow-up resolves it — adopting the placeholder when the times match (its
    ghost/splits are this run's) or merging them into the real row. Within a
     known compound: a missing row is created; a strictly faster time replaces
     it (the ghost belongs to the best run, so it is replaced too when carried);
     a submission at the stored ms — or up to _BEST_TOL_MS above it, filed within
     _SAME_RUN_WINDOW_S of the record being set (the channels read the same game
     clock as round vs truncate) — is the same run arriving over the second
     channel: it only fills in pieces the first one lacked (ghost / splits /
     category); a genuinely slower attempt never touches the record. Missing key pieces skip entirely.
     run_id (the producing StageRun row) follows the same rules: set on new
     rows, replaced when this run becomes the best, absorbed on the equal-ms
     follow-up — so it always points at the run whose payload is stored."""
    if not username or not vehicle_id or not track or not time_ms or time_ms <= 0:
        return
    tm = int(time_ms)
    compound = int(tyre_compound or 0)
    rows = db.session.query(TimeTrialEntry).filter_by(
        vehicle_id=vehicle_id, track=str(track)[:8],
        conditions=str(conditions)[:8], username=username).all()
    splits_json = json.dumps(splits) if isinstance(splits, list) and splits else None

    vessel = None  # this run's unresolved compound=0 placeholder, if any
    if compound > 0:
        entry = next((r for r in rows if r.tyre_compound == compound), None)
        vessel = next((r for r in rows
                       if r.tyre_compound == 0 and r.stage_time_ms == tm), None)
        if vessel is None and entry is None:
            # The snapshot's ms can differ from the PostTime's by a millisecond,
            # so the exact match above misses. At most one placeholder exists per
            # 4-tuple (the unique key includes compound), so any zero row in this
            # group is this run's — adopt it instead of filing a second row that
            # would orphan it and later crash the boot backfill. (When a real
            # row for this compound already exists the placeholder is left alone:
            # its payload may belong to an older, never-resolved run, and the
            # boot backfill settles those without risk of mixing payloads.)
            vessel = next((r for r in rows if r.tyre_compound == 0), None)
        if vessel is not None:
            if entry is None:
                # First record of this run with a known compound — adopt the
                # placeholder as the real row; its ghost/splits are this run's.
                vessel.tyre_compound = compound
                entry = vessel
            else:
                # The placeholder holds this run's payload (the PostTime that
                # filed it) — merge it into the call vars so the branches below
                # store it, then drop the placeholder.
                if not ghost_b64 and vessel.ghost_data_b64:
                    ghost_b64 = vessel.ghost_data_b64
                if splits_json is None and vessel.splits_json:
                    splits_json = vessel.splits_json
    else:
        # Unknown compound (a PostTime): land on this run's row when it exists
        # (channel inversion), else reuse the 4-tuple's placeholder — at most
        # one, since pre-compound rows were keyed without it — else create it.
        entry = next((r for r in rows if r.stage_time_ms == tm), None) \
            or next((r for r in rows if r.tyre_compound == 0), None)
        if entry is None and rows:
            # Channel inversion + rounding: the stage-detail half often files
            # this run FIRST, and its ms can differ from the PostTime's by a
            # millisecond (round vs truncate of the same game clock), so the
            # exact match above misses and this would file a second row that
            # orphans the first. Fall back to the nearest row in the group — a
            # genuinely different run is never within _BEST_TOL_MS on the same
            # car + track + conditions (re-entry alone takes over a minute).
            entry = min(rows, key=lambda r: abs((r.stage_time_ms or 0) - tm))
            if abs((entry.stage_time_ms or 0) - tm) > _BEST_TOL_MS:
                entry = None

    if entry is None:
        db.session.add(TimeTrialEntry(
            vclass=str(vclass or '0')[:8], track=str(track)[:8],
            conditions=str(conditions)[:8], category=int(category or 0),
            username=username, stage_time_ms=tm, vehicle_id=vehicle_id,
            livery_id=livery_id, tyre_compound=compound,
            using_wheel=using_wheel, using_assists=using_assists,
            ghost_data_b64=ghost_b64 or None, splits_json=splits_json,
            run_id=run_id))
        return

    if tm < (entry.stage_time_ms or 0):
        entry.stage_time_ms = tm
        if vclass:
            entry.vclass = str(vclass)[:8]
        if category:
            entry.category = int(category)
        if livery_id is not None:
            entry.livery_id = livery_id
        if using_wheel is not None:
            entry.using_wheel = using_wheel
        if using_assists is not None:
            entry.using_assists = using_assists
        # The ghost + splits describe the best run itself — take what THIS run
        # carried (NULL when it had none; the equal-ms follow-up from the other
        # channel can still fill them in).
        entry.ghost_data_b64 = ghost_b64 or None
        entry.splits_json = splits_json
        # The stored payload is now THIS run's — point the run pointer at it
        # when we know it (the equal-ms follow-up settles it otherwise).
        if run_id is not None:
            entry.run_id = run_id
        entry.submitted_at = utcnow()
    else:
        # Not strictly faster — the same run over its second channel, or a
        # slower attempt? Exact ms equality says same run; a hair above (up to
        # _BEST_TOL_MS) does too WHEN the stored row was filed moments ago:
        # the channels read the same game clock as round vs truncate, so the
        # snapshot's ms can sit a millisecond over the PostTime's, and one run's
        # two channels file seconds apart (a TT's snapshot-only half waits out
        # the client's 45 s flush). A genuinely different, slightly slower
        # attempt at the same stage can't file within _SAME_RUN_WINDOW_S of the
        # record being set — re-entry plus another full run takes minutes — so
        # without that wall-clock proximity a slower time never touches the
        # record. (Production 2026-11-26: a TT's PostTime filed comp=1 at
        # 157707, its snapshot follow-up carried 157708 — neither branch fired
        # and run_id stayed NULL.)
        stored_ms = entry.stage_time_ms or 0
        same_run = tm == stored_ms or (
            0 < tm - stored_ms <= _BEST_TOL_MS
            and entry.submitted_at is not None
            and (utcnow() - entry.submitted_at).total_seconds()
            <= _SAME_RUN_WINDOW_S)
        if same_run:
            # Same run, second channel: absorb only what the stored row lacks.
            changed = False
            if ghost_b64 and not entry.ghost_data_b64:
                entry.ghost_data_b64 = ghost_b64
                changed = True
            if splits_json is not None and entry.splits_json is None:
                entry.splits_json = splits_json
                changed = True
            if category and not entry.category:
                entry.category = int(category)
                changed = True
            # The follow-up (stage-detail after the PostTime placeholder, or a
            # faster-run's snapshot after its PostTime) carries the run id —
            # replace whatever was stored so the pointer matches the payload.
            if run_id is not None and entry.run_id != run_id:
                entry.run_id = run_id
                changed = True
            if changed:
                entry.submitted_at = utcnow()

    if vessel is not None and vessel is not entry:
        db.session.delete(vessel)


# Rallycross best rows are single-lap PostTime records — no stage-run row ever
# exists for an individual lap, so _upsert_best can never point them at the run
# that drove it. Tolerance for matching a stored lap against one of the
# finishing run's per-lap times when stamping its run_id (the game's own
# PostTime floats, exact to the ms apart from scaling rounding).
_RX_LAP_MATCH_MS = 250


def _stamp_rx_run_id(username: str, vehicle_id: int, track_model_id: int,
                     conditions: str, laps_s: list | None, run_id: int) -> None:
    """Point every still-unattributed best row in the group at the run that
    drove its stored lap time (within _RX_LAP_MATCH_MS). laps_s are per-lap
    times in seconds (the splits_json convention); rows store ms. Rows already
    pointing at a run are left alone — their payload may belong to an older
    run whose lap merely landed within tolerance of this one."""
    if not laps_s:
        return
    for row in db.session.query(TimeTrialEntry).filter(
            TimeTrialEntry.username == username,
            TimeTrialEntry.vehicle_id == vehicle_id,
            TimeTrialEntry.track == str(track_model_id)[:8],
            TimeTrialEntry.conditions == str(conditions)[:8],
            TimeTrialEntry.run_id.is_(None)).all():
        if any(abs((row.stage_time_ms or 0) - lap * 1000) <= _RX_LAP_MATCH_MS
               for lap in laps_s):
            row.run_id = run_id


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
    # Heavy per-stage payloads — stored in club_stage_states, never inside the
    # result row's JSON (kept out of every merge so legacy residue can't leak
    # back in either).
    _heavy_keys = {'tuning_setup_b64', 'comp_damage', 'vehicle_mud'}

    if mark_dnf:
        # DNF close-out marker (backlog 32): the game pre-persisted a stage's
        # time limit via its post-wreck leaderboard query, so keep stored times
        # and only flag entries as non-finishes. Never burns an attempt — a
        # mid-leg DNF must not lock out the rest of the leg. dnf_stages (step 3)
        # lists every skipped stage — from the wreck up to the next service
        # area, which in Multi-Location mode can span later legs (each such
        # entry carries its own event_index; absent = this marker's leg) — with
        # its max time: each gets flagged, and any without a stored pre-
        # persisted value is given the limit, so the count-based progress
        # resumes right after the last flagged stage.
        rs = int(data.get('race_status') or 4)
        dnf_list = data.get('dnf_stages')
        targets: list[tuple[int, int]] = []
        if isinstance(dnf_list, list):
            for item in dnf_list:
                try:
                    si = int(item.get('stage_index'))
                    t = int(item.get('time_ms') or 0)
                    ei = item.get('event_index')
                    if ei is None:
                        ei = event_index
                    p = global_stage_index(event, int(ei), si)
                except (TypeError, ValueError):
                    continue
                if p >= 0:
                    targets.append((p, max(0, t)))
        if not targets:
            # Legacy single-marker shape (older clients / unresolved plan):
            # flag the close-out stage and, when given, the wrecked one.
            targets.append((pos, 0))
            dnf_local = data.get('dnf_stage_index')
            if dnf_local is not None:
                try:
                    dnf_pos = global_stage_index(event, event_index, int(dnf_local))
                except (TypeError, ValueError):
                    dnf_pos = -1
                if 0 <= dnf_pos < pos:
                    targets.append((dnf_pos, 0))
        vid = None
        # A service-area retirement happens BEFORE any stage-begin capture, so
        # the entries may have no vehicle data at all — the close-out payload
        # itself carries the car the player was driving; merge it into every
        # flagged entry that lacks one (leaderboards + next-leg carry-over).
        sent_vid = data.get('vehicle_id') or None
        sent_liv = data.get('livery_id') or None
        sent_nat = data.get('nationality_id') or None
        for p, t in targets:
            while len(stages) <= p:
                stages.append(
                    {'time_ms': 0, 'penalties_ms': 0, 'submitted_at': None})
            entry = {
                **stages[p],
                **{k: v for k, v in
                   in_progress_all.get(str(p), {}).items() if v is not None},
            }
            if not entry.get('vehicle_id') and sent_vid:
                entry['vehicle_id'] = sent_vid
            if not entry.get('livery_id') and sent_liv:
                entry['livery_id'] = sent_liv
            if not entry.get('nationality_id') and sent_nat:
                entry['nationality_id'] = sent_nat
            entry['race_status'] = rs
            # Markers carry no submitted_at of their own, but the home page's
            # "days without a terminal damage" card only counts entries that
            # have one — stamp the close-out moment (a real submission's
            # timestamp is never overwritten).
            if not entry.get('submitted_at'):
                entry['submitted_at'] = utcnow().isoformat()
            if t > 0 and not (entry.get('time_ms', 0) or 0):
                entry['time_ms'] = t
            entry.setdefault('time_ms', 0)
            entry.setdefault('penalties_ms', 0)
            for k in _heavy_keys:
                entry.pop(k, None)
            stages[p] = entry
            if not vid:
                vid = entry.get('vehicle_id') or sent_vid
    else:
        # Merge oldest→newest so newer wins: prior submission, then stage-begin
        # capture, then this submission's explicitly-sent fields. Heavy keys
        # are excluded — they live in club_stage_states (re-attached on read).
        stage_entry = {
            **{k: v for k, v in prev_stage_entry.items() if k not in _heavy_keys},
            **{k: v for k, v in in_progress_entry.items()
               if v is not None and k not in _heavy_keys},
            **{k: v for k, v in data.items()
               if v is not None and k not in _routing_keys
               and k not in _heavy_keys},
        }
        stage_entry['time_ms'] = time_ms
        stage_entry['submitted_at'] = utcnow().isoformat()
        if vehicle_id is not None:
            stage_entry['vehicle_id'] = vehicle_id
        stage_entry.setdefault('penalties_ms', 0)
        stage_entry.setdefault('race_status', 0)
        vid = vehicle_id if vehicle_id is not None else stage_entry.get('vehicle_id')
        stages[pos] = stage_entry
        # Set-only-provided: a partial submission keeps the stored values.
        if data.get('comp_damage') is not None:
            upsert_stage_state(event_id, user.username, pos,
                               comp_damage=data['comp_damage'])
        if data.get('vehicle_mud') is not None:
            upsert_stage_state(event_id, user.username, pos,
                               vehicle_mud=data['vehicle_mud'])
        if data.get('tuning_setup_b64') is not None:
            upsert_stage_state(event_id, user.username, pos,
                               tuning_b64=data['tuning_setup_b64'])

    # The consumed stage-begin captures are merged — drop them so
    # in_progress_json holds only the currently-running stage (it used to
    # accumulate a second copy of every tuning blob, forever).
    if mark_dnf:
        for p, _t in targets:
            in_progress_all.pop(str(p), None)
    else:
        in_progress_all.pop(str(pos), None)
    r.in_progress_json = json.dumps(in_progress_all)

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


@game_api_bp.route('/api/game/stats-record', methods=['POST'])
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_stats_record():
    """Retired (#56): lifetime counters and per-track bests are now computed
    from the append-only stage_runs table, which the client feeds for EVERY run
    (club + proxied official/TT) via /api/game/stage-detail. The route stays as
    a no-op so older clients' fire-and-forget POSTs don't 404 during the update
    window."""
    return jsonify({'ok': True})


# Fields only the StageEnded snapshot half carries — used to score how complete
# a stage-detail record is, so a merged row always beats a half-merged one when
# the game's retransmitted RPC arrives out of order.
_DETAIL_RICH_FIELDS = (
    'track_id', 'track_model_id', 'location_id', 'vehicle_class_id',
    'input_library', 'input_device', 'input_name',
    'cam_bonnet_m', 'cam_bumper_m', 'cam_chase_close_m', 'cam_chase_far_m',
    'cam_dash_m', 'cam_head_m',
    'g_days_played', 'g_seconds_played', 'g_session_count',
)


def _detail_rich_score(record) -> int:
    """Count of snapshot-only fields present (non-null, non-zero) on a record."""
    n = 0
    for key in _DETAIL_RICH_FIELDS:
        value = record.get(key) if isinstance(record, dict) else getattr(record, key, None)
        if value is None or (isinstance(value, (int, float)) and value == 0):
            continue
        n += 1
    return n


@game_api_bp.route('/api/game/stage-detail', methods=['POST'])
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_stage_detail():
    """Record a merged per-stage run detail posted by the client (the StageEnded
    telemetry snapshot paired with the StageComplete RPC — see
    dr2server/stage_detail.py).

    Append-only: every run lands as its own stage_runs row, so profile stats are
    plain aggregations. Half-merged records (only one of the two sources ever
    arrived) store whatever they carry — every column but username is optional.
    A retransmitted StageComplete for a stage slot recorded within the last 2
    minutes collapses into that row instead of adding a duplicate.
    """
    data = request.get_json(silent=True) or {}
    user = g.game_user

    def _int(key: str):
        try:
            v = data.get(key)
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _float(key: str) -> float:
        try:
            v = data.get(key)
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _bool(key: str) -> bool:
        return bool(data.get(key))

    # Time: prefer the RPC's exact milliseconds; fall back to the snapshot's
    # seconds.
    time_ms = _int('stage_time_ms') or 0
    if not time_ms:
        time_ms = int(round(_float('stage_time_seconds') * 1000))

    # Distance: the RPC's per-stage integer is exact (MetersDriven is cumulative
    # across stages, so it is never a source); fall back to the camera-metres
    # sum. DNF close-outs carry their genuine partial values — an RPC-only half
    # (no snapshot) simply has none and stays None naturally.
    end_status = _int('player_end_status') or 0
    race_status = _int('race_status')
    player_finished = _bool('player_finished')
    # Lapped rallycross finishes report player_end_status 10/48/77 while still
    # finished; every real DNF carries player_finished=0, so the end-status code
    # only counts when the game says the driver did not finish.
    is_dnf = ((end_status != 0 and not player_finished)
              or (race_status is not None and race_status != 0)
              or bool(data.get('recov_to_service')))
    distance_m = _int('distance_driven') or 0
    if not distance_m:
        distance_m = int(round(sum(
            _float(k) for k in (
                'metres_cam_bonnet', 'metres_cam_bumper', 'metres_cam_chase_close',
                'metres_cam_chase_far', 'metres_cam_dash', 'metres_cam_head',
            ))))
    if distance_m == 0:
        # No channel reported a value (a half that never carried any) — unknown.
        distance_m = None

    # Source type from the snapshot's challenge_type (-1 TT / 1 official / 2 club).
    # A half-merged record without the snapshot lacks it, but a present event_id
    # is decisive — only club runs carry one.
    source_type = {-1: 'timetrial', 1: 'official', 2: 'club'}.get(
        _int('challenge_type'), 'unknown')
    if source_type == 'unknown' and data.get('event_id'):
        source_type = 'club'

    fields = {
        'source_type': source_type,
        'event_id': str(data['event_id'])[:64] if data.get('event_id') else None,
        'challenge_id': _int('challenge_id'),
        'event_index': _int('event_index') or 0,
        'stage_index': _int('stage_index') or 0,
        'track_id': _int('track_id'),
        'track_model_id': _int('track_model_id'),
        'location_id': _int('location_id'),
        'vehicle_id': _int('vehicle_id'),
        'vehicle_class_id': _int('vehicle_class_id'),
        'time_ms': time_ms,
        'player_finished': _bool('player_finished'),
        'player_end_status': end_status,
        'race_position': _int('race_position') or 0,
        'distance_m': distance_m,
        'using_wheel': _bool('using_wheel'),
        'using_assists': _bool('using_assists'),
        'hardcore_damage': _bool('is_hardcore_damage_enabled'),
        'assists_enabled': _bool('is_any_assists_enabled'),
        'input_library': str(data['library_id'])[:64] if data.get('library_id') else None,
        'input_device': str(data['device_hardware_id'])[:128] if data.get('device_hardware_id') else None,
        'input_name': str(data['driver_name'])[:128] if data.get('driver_name') else None,
        'tyre_compound': _int('tyre_compound_id') or 0,
        'tyre_wear': _float('tyre_wear_state'),
        'surface_degradation_enabled': _bool('surface_degradation_enabled'),
        'surface_degradation_level': _float('surface_degradation_level'),
        'weather_preset_id': _int('weather_preset_id') or 0,
        'conditions_id': _int('stage_conditions_id'),
        'vr_enabled': _bool('vr_enabled'),
        'cam_bonnet_m': _float('metres_cam_bonnet'),
        'cam_bumper_m': _float('metres_cam_bumper'),
        'cam_chase_close_m': _float('metres_cam_chase_close'),
        'cam_chase_far_m': _float('metres_cam_chase_far'),
        'cam_dash_m': _float('metres_cam_dash'),
        'cam_head_m': _float('metres_cam_head'),
        'g_days_played': _int('g_separate_days_played_count') or 0,
        'g_seconds_played': _int('g_total_seconds_played') or 0,
        'g_session_count': _int('g_session_count') or 0,
        'splits_json': (json.dumps(data['splits'])
                        if isinstance(data.get('splits'), list) and data['splits']
                        else None),
        'flat_stage_index': _int('flat_stage_index'),
    }

    # Normalized once at ingest so leaderboard filters never re-derive them:
    # input method from the raw library id, camera position from whichever
    # camera accumulated metres this stage (the active one).
    library = fields['input_library'] or ''
    if library == 'lib_keyboard':
        fields['input_method'] = 'keyboard'
    elif library == 'lib_direct_input':
        fields['input_method'] = 'wheel'
    else:
        fields['input_method'] = 'controller' if library else None
    cam_metres = {
        'bonnet': fields['cam_bonnet_m'], 'bumper': fields['cam_bumper_m'],
        'chase_close': fields['cam_chase_close_m'],
        'chase_far': fields['cam_chase_far_m'],
        'dash': fields['cam_dash_m'], 'head': fields['cam_head_m'],
    }
    # The camera that accumulated the most metres this stage is the active one;
    # an RPC-only half (no snapshot) has none and stays None.
    fields['camera_position'] = (max(cam_metres, key=cam_metres.get)
                                  if any(cam_metres.values()) else None)

    # The game can retransmit a StageComplete (observed at +15 s); when the
    # client buffer's timeout flushes a half row in between, one run arrives
    # twice. Collapse repeats of the same stage slot within the window into one
    # row — the more complete record (a merged snapshot+RPC beats any half) wins.
    existing = None
    if fields['challenge_id'] is not None:
        window_start = utcnow() - timedelta(seconds=120)
        q = db.session.query(StageRun).filter(
            StageRun.username == user.username,
            StageRun.challenge_id == fields['challenge_id'],
            StageRun.event_index == fields['event_index'],
            StageRun.stage_index == fields['stage_index'],
            StageRun.received_at >= window_start,
        )
        if fields['challenge_id'] == 0:
            # TT runs all carry challenge_id=0 — disambiguate by car + track
            # and time so back-to-back repeats don't collapse into one row.
            q = q.filter(StageRun.track_model_id == fields['track_model_id'],
                         StageRun.vehicle_id == fields['vehicle_id'])
        for cand in q.order_by(StageRun.received_at.desc()).all():
            # Non-TT slots always collapse; TT rows must also be within
            # tolerance on time (a back-to-back repeat is NOT a retransmit).
            if fields['challenge_id'] != 0 or \
                    abs((cand.time_ms or 0) - time_ms) <= _BEST_TOL_MS:
                existing = cand
                break
    same_run = existing is not None
    run_row = existing
    if same_run:
        if _detail_rich_score(fields) > _detail_rich_score(existing):
            for key, value in fields.items():
                setattr(existing, key, value)
    else:
        run_row = StageRun(username=user.username, **fields)
        db.session.add(run_row)

    # Personal best: a clean finish updates the driver's best for car + track
    # + conditions. The PostTime that preceded this StageComplete usually got
    # there first (with the ghost) at the SAME ms — then this call only absorbs
    # the splits; when the PostTime POST failed, it lands the best outright.
    if (not is_dnf and time_ms > 0 and fields['vehicle_id']
            and fields['track_model_id']):
        db.session.flush()
        _upsert_best(
            user.username, fields['vehicle_id'], fields['track_model_id'],
            fields['conditions_id'] or '0', time_ms,
            vclass=fields['vehicle_class_id'],
            livery_id=data.get('livery_id') or None,
            using_wheel=data.get('using_wheel'),
            using_assists=data.get('using_assists'),
            tyre_compound=fields['tyre_compound'],
            splits=json.loads(fields['splits_json']) if fields['splits_json'] else None,
            run_id=run_row.id)
        # Rallycross: the best row is a single lap with no stage-run of its own —
        # point it at this run when one of this run's laps set the record.
        try:
            loc = Track(int(fields['track_model_id'])).location
        except (ValueError, TypeError):
            loc = None
        if loc is not None and loc.display_name in RX_LOCATIONS:
            _stamp_rx_run_id(
                user.username, fields['vehicle_id'], fields['track_model_id'],
                fields['conditions_id'] or '0',
                json.loads(fields['splits_json']) if fields['splits_json'] else None,
                run_row.id)
    db.session.commit()

    return jsonify({'ok': True})


@game_api_bp.route('/api/game/time-trial-submit', methods=['POST'])
@csrf.exempt  # type: ignore[untyped-decorator]
@game_auth_required
def api_game_time_trial_submit():
    """Record a completed stage from the client's listen-in on
    TimeTrial.PostTime (the request is still proxied to EGO untouched, so the
    real global board keeps working; this files the driver's personal best for
    car + track + conditions on this site — every distinct car keeps its own
    row, with the vehicle class stored alongside). The game fires a PostTime
    after EVERY completed stage (club, official and time trials alike), so this
    is the primary best-recording path; the run's ghost rides along. The
    compound is unknown here (the PostTime never carries one) unless the client
    sends it, so this usually files a compound=0 placeholder that the run's
    stage-detail follow-up resolves. Rules in _upsert_best: strictly faster
    replaces (ghost included), equal-ms absorbs missing pieces, slower is
    ignored."""
    data = request.get_json(silent=True) or {}
    user = g.game_user
    try:
        track = str(data.get('track_model_id') or '0')[:8]
        conditions = str(data.get('conditions_id') or '0')[:8]
        category = int(data.get('category') or 0)
        vehicle_id = int(data.get('vehicle_id') or 0)
        time_ms = int(data.get('stage_time_ms') or 0)
    except (TypeError, ValueError):
        return _api_error('bad time trial payload')
    if time_ms <= 0:
        return _api_error('missing stage time')
    if not vehicle_id:
        return _api_error('missing car id')

    _upsert_best(
        user.username, vehicle_id, track, conditions, time_ms,
        category=category, vclass=data.get('vehicle_class_id'),
        livery_id=int(data.get('livery_id') or 0),
        using_wheel=bool(data.get('using_wheel')),
        using_assists=bool(data.get('using_assists')),
        ghost_b64=data.get('ghost_data_b64'), splits=data.get('splits'),
        tyre_compound=data.get('tyre_compound'))
    db.session.commit()
    return jsonify({'ok': True})


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
