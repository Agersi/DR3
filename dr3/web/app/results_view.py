"""View helpers for championship results on the club/event detail pages.

Both pages share these: per-round (leg) windows and statuses, the overall
standings with points, the per-round classification, and stage-by-stage
result grids.  All data comes from the Result rows the game endpoints store;
scoring reuses ``scoring.py`` so the site shows exactly what the in-game
leaderboard computes.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from .class_thumbs import class_thumb_url
from .countries import flag_url
from .locations import location_thumb_url
from .scoring import (
    adjusted_total_ms,
    car_label,
    championship_points,
    championship_points_breakdown,
    championship_points_rounds,
    championship_view,
    flat_stage_bogeys_ms,
    fresh_tyre_start,
    overall_standings,
    power_stage_enabled,
    rally_standings,
    scoring_system_for,
    tyre_set_changed,
    vehicle_meta,
)
from .tables import (
    CHAMP_MODE_SEASON,
    RX_LOCATIONS,
    drivetrains_for_class,
    enrich_stage,
    leg_duration,
    normalize_championship,
)

from dr2server.game_data import rallycross_stage_plan  # noqa: E402
from dr2server.stage_limits import split_count_for_limit, stage_limit_ms  # noqa: E402
from dr2server.tuning import TuningBlob  # noqa: E402

SCORING_LABELS = {'wrc': 'WRC', 'groupb': 'Group B', 'codemasters': 'Codemasters'}
MODE_LABELS = {'multi': 'Multi-Location Rally', 'season': 'Rally Season'}

_DT_FMT = '%a %d %b %Y, %H:%M'


def mode_of(event: dict | None) -> str:
    m = ((event or {}).get('settings') or {}).get('mode')
    return m if m in ('multi', 'season') else 'multi'


def mode_label(event: dict | None) -> str:
    return MODE_LABELS.get(mode_of(event), MODE_LABELS['multi'])


def scoring_label(event: dict | None) -> str:
    return SCORING_LABELS.get(scoring_system_for(event), SCORING_LABELS['wrc'])


def format_ms(ms) -> str:
    """Human rally time: ``M:SS.mmm`` or ``H:MM:SS.mmm``; '' for 0/None."""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return ''
    if ms <= 0:
        return ''
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    if h:
        return f'{h}:{m:02d}:{s:02d}.{msec:03d}'
    return f'{m}:{s:02d}.{msec:03d}'


def gap_text(ms) -> str:
    """Gap to the leader as ``+M:SS.mmm``; '' when the row is the leader."""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return ''
    if ms <= 0:
        return ''
    return '+' + format_ms(ms)


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def leg_windows(event: dict) -> list[tuple[datetime | None, datetime | None]]:
    """(start, end) per round.  Rally Season: back-to-back windows from the
    championship start (each leg's own duration); Multi-Location Rally: every
    round shares the whole-championship window."""
    champ = normalize_championship(event)
    start = _parse_dt(champ.get('start_time'))
    end = _parse_dt(champ.get('end_time'))
    if mode_of(champ) == CHAMP_MODE_SEASON and start is not None:
        windows = []
        cur = start
        for ev in champ['events']:
            d = leg_duration(ev)
            windows.append((cur, cur + d))
            cur += d
        return windows
    return [(start, end)] * len(champ['events'])


def effective_end(event: dict, now: datetime) -> datetime | None:
    """The "ends" time used to order clubs/events and for live countdowns.

    Rally Season legs run back-to-back, so the stored ``end_time`` is the LAST
    round's end — a live season's current round ends much sooner, and ordering
    by the stored value buries an active season behind slower single-window
    championships (e.g. a season whose current leg ends in 3d listed after a
    rally that ends in 4.8d). For seasons this returns the current round's
    window end (the first round's if the season has not started yet);
    Multi-Location rallies share one window across all rounds, so for them —
    and legacy events — the stored ``end_time`` is already correct."""
    champ = normalize_championship(event)
    windows = leg_windows(champ)
    fallback = _parse_dt((event or {}).get('end_time'))
    if not windows:
        return fallback
    idx = 0
    for i, (ws, _we) in enumerate(windows):
        if ws is None or now >= ws:
            idx = i
        else:
            break
    end = windows[idx][1]
    return end if end is not None else fallback


def current_leg_index(event: dict, now: datetime) -> int:
    """The leg a Rally Season is in right now (0 before the first window)."""
    windows = leg_windows(normalize_championship(event))
    idx = 0
    for i, (ws, _we) in enumerate(windows):
        if ws is None or now >= ws:
            idx = i
        else:
            break
    return idx


def current_event_scope(event: dict, now: datetime) -> tuple[int, int]:
    """Flat stage range ``(offset, count)`` of the championship's CURRENT window.

    Rally Seasons run leg by leg, so the scope is the active leg; Multi-Location
    rallies and legacy events share one window across the whole championship."""
    rallies = championship_view(event)
    if mode_of(event) == CHAMP_MODE_SEASON:
        r = rallies[current_leg_index(event, now)]
        return r['offset'], r['count']
    return 0, sum(r['count'] for r in rallies)


def scope_participation(entry: dict, offset: int, count: int) -> tuple[int, int, bool]:
    """Per-stage state within a flat stage range for the dashboard Drivers column.

    Returns ``(finished, closed, touched)``.  A stage is *finished* with a real
    time and no DNF race_status; it is *closed* when finished OR terminally
    DNF'd (any race_status — those stages can never be driven again, so they do
    not block "done" the way the in-game resume logic treats them); it is
    *touched* when it carries any time or DNF marker (a pre-persisted limit
    value still proves the driver was in that stage)."""
    seg = (entry.get('stages') or [])[offset:offset + count]
    finished = 0
    closed = 0
    touched = False
    for s in seg:
        if not s:
            continue
        t = int(s.get('time_ms', 0) or 0)
        rs = int(s.get('race_status', 0) or 0)
        if t > 0 or rs != 0:
            touched = True
        if rs != 0:
            closed += 1
        elif t > 0:
            finished += 1
            closed += 1
    return finished, closed, touched


def event_profile(event: dict | None, now: datetime) -> dict | None:
    """Filterable profile of a championship for the club list page.

    Drivetrain and location come from the active leg — seasons switch legs
    (and venues) over time, Multi-Location rallies run one class throughout
    (the first leg's) but every round at once, so their location lists all
    venues in round order (deduped)."""
    if not event:
        return None
    champ = normalize_championship(event)
    settings = champ['settings']
    legs = champ['events']
    idx = current_leg_index(event, now) if champ['mode'] == CHAMP_MODE_SEASON else 0
    car_class = (legs[idx].get('car_class') or '') if legs else ''
    if champ['mode'] == CHAMP_MODE_SEASON:
        location = (legs[idx].get('location') or '') if legs else ''
    else:
        venues = []
        for leg in legs:
            loc = leg.get('location') or ''
            if loc and loc not in venues:
                venues.append(loc)
        location = ' - '.join(venues)
    return {
        'mode': champ['mode'],
        'hardcore_damage': bool(settings.get('hardcore_damage')),
        'external_cameras': not bool(settings.get('force_cockpit_camera')),
        'allow_assists': bool(settings.get('allow_assists')),
        'car_class': car_class,
        'location': location,
        'drivetrains': drivetrains_for_class(car_class),
    }


def window_status(start: datetime | None, end: datetime | None, now: datetime) -> str:
    """'upcoming' / 'live' / 'ended' for a (start, end) window."""
    if start is not None and now < start:
        return 'upcoming'
    if end is not None and now > end:
        return 'ended'
    return 'live'


def event_status(event: dict, now: datetime) -> str:
    """'upcoming' / 'live' / 'ended' for a stored event's own window."""
    return window_status(_parse_dt(event.get('start_time')),
                         _parse_dt(event.get('end_time')), now)


def window_text(start: datetime | None, end: datetime | None) -> str:
    if start is None and end is None:
        return ''
    s = start.strftime(_DT_FMT) if start else '?'
    e = end.strftime(_DT_FMT) if end else '?'
    return f'{s} → {e}'


def display_stage(location: str, s: dict) -> dict:
    """Stage row for templates: stored fields plus resolved name/distance/
    conditions (raw builder stages only carry ids; legacy rows may already
    carry labels — those are kept when the lookup comes up empty)."""
    e = enrich_stage(location, s or {})
    out = dict(s or {})
    for key in ('name', 'distance_km', 'conditions'):
        if e.get(key):
            out[key] = e[key]
    return out


def championship_rounds(event: dict, now: datetime) -> list[dict]:
    """One row per round (leg): display info, enriched stages, window and
    status.  ``offset``/``count`` are the flat stage ordinals so result rows
    can slice the stored flat stages array."""
    champ = normalize_championship(event)
    windows = leg_windows(champ)
    out: list[dict] = []
    for rally in championship_view(champ):
        i = rally['index']
        start, end = windows[i]
        out.append({
            'index': i,
            'location': rally['location'],
            'car_class': rally['car_class'],
            'duration_label': rally['duration_label'],
            'offset': rally['offset'],
            'count': rally['count'],
            'stages': [display_stage(rally['location'], s) for s in rally['stages']],
            'window_start_iso': start.isoformat() if start else None,
            'window_end_iso': end.isoformat() if end else None,
            'window_text': window_text(start, end),
            'status': window_status(start, end, now),
            'is_rx': rally['location'] in RX_LOCATIONS,
        })
    return out


def overall_table(event: dict, entries: list[dict]) -> list[dict]:
    """Championship standings for the site: points first (``championship_points``),
    bogey-adjusted time as tiebreaker — the same classification the in-game
    leaderboard uses.  Only drivers with at least one real stage time are
    listed; rows carry pre-formatted time/gap strings."""
    points = championship_points(event, entries)
    rows = [r for r in overall_standings(event, entries) if r['stages_done'] > 0]
    leader_time = min((r['total_time_ms'] for r in rows), default=0)
    for r in rows:
        r['points'] = points.get(r['username'], 0)
        r['gap_ms'] = r['total_time_ms'] - leader_time
        r['time_text'] = format_ms(r['total_time_ms'])
        r['gap_str'] = gap_text(r['gap_ms'])
    rows.sort(key=lambda r: (-r['points'], r['total_time_ms'], -r['stages_done']))
    return rows


def championship_points_table(event: dict, entries: list[dict]) -> dict:
    """Championship points table for the club page's events tab.

    One row per driver who ran at least one stage (same population and order as
    ``overall_table``), a cell per points round with that round's classification
    points and power-stage bonus, plus the total across rounds. Points rounds
    follow the championship mode: Rally Seasons show one column per leg;
    Multi-Location rallies and legacy events are classified once over the whole
    event (one column, all locations in its header)."""
    from .models import User

    rounds = championship_points_rounds(event)
    order = overall_table(event, entries)
    breakdown = championship_points_breakdown(event, entries)
    flag_by_user: dict = {}
    wanted = {r['username'] for r in order if r.get('username')}
    if wanted:
        for u in User.query.filter(User.username.in_(wanted)).all():
            flag_by_user[u.username] = flag_url(u.country or '')
    rows = []
    for i, r in enumerate(order):
        cells = breakdown.get(r['username']) or [{'points': 0, 'bonus': 0} for _ in rounds]
        rows.append({
            'pos': i + 1,
            'username': r['username'],
            'driver_url': f"/profile/{r['username']}",
            'flag_url': flag_by_user.get(r['username']) or flag_url(''),
            'vehicle': r.get('car') or '',
            'cells': cells,
            'total': r['points'],
        })
    return {
        'rounds': [{'index': rd['index'], 'label': rd['label'], 'location': rd['location']}
                   for rd in rounds],
        'rows': rows,
        'power_stage': power_stage_enabled(event),
    }


def _ran_any_stage(row: dict) -> bool:
    """Whether a rally-standings row has a real time on at least one stage of
    the round (i.e. the driver actually took part in it)."""
    return any(s and int(s.get('time_ms', 0) or 0) > 0 for s in row['stages'])


def round_results(rally: dict, entries: list[dict]) -> dict:
    """One round's results: its classification (participants only, DNFs flagged)
    plus a per-stage grid — every driver with a real time on that stage,
    fastest first, with the gap to the stage leader."""
    standings = [r for r in rally_standings(entries, rally) if _ran_any_stage(r)]
    for r in standings:
        r['time_text'] = format_ms(r['total_time_ms'])
    stages_out: list[dict] = []
    for j, s in enumerate(rally['stages']):
        flat_idx = rally['offset'] + j
        results: list[dict] = []
        for e in entries:
            seg = e.get('stages') or []
            st = seg[flat_idx] if flat_idx < len(seg) else None
            t = int((st or {}).get('time_ms', 0) or 0)
            if t <= 0:
                continue
            # Per-run extras stored on the stage entry (club submissions carry
            # them; official/TT rows don't — both render blank then).
            results.append({
                'username': e.get('username', ''),
                'car': car_label(e),
                'time_ms': t + int((st or {}).get('penalties_ms', 0) or 0),
                'wheel': bool((st or {}).get('using_wheel')),
                'distance_m': int(
                    (st or {}).get('distance_driven')
                    or (st or {}).get('meters_driven') or 0),
            })
        results.sort(key=lambda x: x['time_ms'])
        leader = results[0]['time_ms'] if results else 0
        for i, row in enumerate(results):
            row['pos'] = i + 1
            row['time_text'] = format_ms(row['time_ms'])
            row['gap_str'] = gap_text(row['time_ms'] - leader)
        stages_out.append({'stage': s, 'results': results})
    return {'standings': standings, 'stages': stages_out}


def club_podium_counts(items: list[tuple[dict, list[dict]]]) -> dict[str, dict]:
    """Per-member podium tallies across a club's events.

    ``items`` is one ``(event, entries)`` pair per championship the club has run.
    Multi-Location rallies (and legacy events) award ONE set of medals for the
    whole event: every driver with at least one real stage time is classified by
    bogey-adjusted total across the whole championship — a DNF takes up its
    position, exactly like the points classification — and the top three earn
    gold / silver / bronze. Rally Seasons keep one set per round, classified the
    same way over each leg. Counts accumulate across all events, so each
    member's totals reflect their whole history with the club."""
    counts: dict[str, dict] = {}
    medals = ('gold', 'silver', 'bronze')
    for event, entries in items:
        if not entries:
            continue
        rallies = championship_view(event)
        if mode_of(event) != CHAMP_MODE_SEASON:
            total = sum(r['count'] for r in rallies)
            if not total:
                continue
            rows = [r for r in overall_standings(event, entries)
                    if r['stages_done'] > 0]
            for i, row in enumerate(rows[:3]):
                bucket = counts.setdefault(
                    row['username'], {'gold': 0, 'silver': 0, 'bronze': 0})
                bucket[medals[i]] += 1
            continue
        for rally in rallies:
            finishers = [r for r in rally_standings(entries, rally)
                         if _ran_any_stage(r)]
            for i, row in enumerate(finishers[:3]):
                bucket = counts.setdefault(
                    row['username'], {'gold': 0, 'silver': 0, 'bronze': 0})
                bucket[medals[i]] += 1
    return counts


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stage_runs_by_user(event_id: str) -> dict:
    """``{username: {flat_stage_index: latest StageRun}}`` for one club event —
    the per-run attribute source (tyre / input / VR / camera / assists / splits)
    for the events-tab board. Rows exist only for runs since the stage-detail
    client shipped; older history has no row and renders blank."""
    from .models import StageRun
    out: dict = {}
    base = _now_utc()
    for run in StageRun.query.filter_by(event_id=event_id).all():
        if run.flat_stage_index is None:
            continue
        per_user = out.setdefault(run.username, {})
        prev = per_user.get(run.flat_stage_index)
        if prev is None or (run.received_at or base) >= (prev.received_at or base):
            per_user[run.flat_stage_index] = run
    return out


def _most_common(values):
    """The value a driver used most often across their runs; ``None`` when they
    never reported it."""
    counts: dict = {}
    for v in values:
        if v is None:
            continue
        counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _run_attrs(run):
    """Per-run attribute dict from one StageRun; all-``None`` when there is no
    stored detail row for that run."""
    if run is None:
        return {'tyre': None, 'input': None, 'vr': None, 'camera': None,
                'assists': None, 'splits': None}
    splits = None
    if run.splits_json:
        try:
            loaded = json.loads(run.splits_json)
            splits = loaded if isinstance(loaded, list) and loaded else None
        except (ValueError, TypeError):
            splits = None
    return {
        'tyre': int(run.tyre_compound) if run.tyre_compound else None,
        'input': run.input_method,
        'vr': bool(run.vr_enabled),
        'camera': run.camera_position,
        'assists': bool(run.assists_enabled or run.using_assists),
        'splits': splits,
    }


def _penalty_ms(stage_entry) -> int:
    """Game-assigned penalties on one stage entry (time + repair). No penalty
    system of our own — these are just displayed."""
    if not stage_entry:
        return 0
    return (int(stage_entry.get('penalties_ms', 0) or 0)
            + int(stage_entry.get('repair_penalty_ms', 0) or 0))


def _spare_tyres(stage_entry):
    """Spare tyre count chosen for one stage. Prefers the value the game
    server parsed at stage-begin (v0.1.74+ clients); falls back to decoding
    the stored tuning blob, which older clients sent too — so historical
    stages show spares as well. ``None`` when unreported (no stage-begin
    capture, e.g. DNF markers)."""
    if not stage_entry:
        return None
    v = stage_entry.get('spare_tyres')
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    blob_b64 = stage_entry.get('tuning_setup_b64')
    if blob_b64:
        try:
            blob = TuningBlob.decode(base64.b64decode(blob_b64))
        except Exception:
            return None
        if blob is not None:
            return blob.spare_tyres()
    return None


def event_board(event: dict, round_index: int, mode: str,
                stage_local_index: int | None = None,
                now: datetime | None = None) -> dict | None:
    """The events-tab leaderboard for a championship.

    Rally Seasons show one round at a time; Multi-Location rallies (and legacy
    events) combine the whole event into ONE board — every leg's stages in a
    single flat bar, Overall accumulating across legs (payload carries
    ``combined`` so the client can hide the round strip).

    ``mode`` is ``'overall'`` (each driver's cumulative time through the
    selected stage — stage 1 alone is just the first stage) or ``'stage'``
    (one stage's board); the stage bar works in both modes. Times and penalties
    come from the authoritative ``club_results`` stage entries; unfinished
    stages of an Overall total take their bogey, exactly like the championship
    standings. The per-run columns come from the matching ``stage_runs`` rows —
    in Overall mode each is the value the driver used most across those
    stages, and tyre lists every distinct compound they ran. The car shown is
    the one actually driven in the selected range (latest stage's run row
    wins), NOT the Result-level vehicle — that column holds the driver's most
    recent car anywhere in the championship, which is wrong on an earlier
    round once a later leg has been driven."""
    from .events import entries_for_event
    from .models import User

    now = now or _now_utc()
    rounds = championship_rounds(event, now)
    if not rounds:
        return None
    # Multi-Location rallies run as ONE championship: the board spans every
    # leg (flat stage bar, Overall accumulates across legs). Rally Seasons
    # stay per-round.
    combined = mode_of(event) != CHAMP_MODE_SEASON
    if combined:
        round_index = 0
        offset, count = 0, sum(r['count'] for r in rounds)
        flat_stages = [(r, s) for r in rounds for s in r['stages']]
    else:
        try:
            round_index = int(round_index)
        except (TypeError, ValueError):
            round_index = 0
        round_index = max(0, min(round_index, len(rounds) - 1))
        rnd = rounds[round_index]
        offset, count = rnd['offset'], rnd['count']
        flat_stages = [(rnd, s) for s in rnd['stages']]
    stages_bar = []
    for k, (r, s) in enumerate(flat_stages):
        item = {'local_index': k, 'label': f'S{k + 1}', 'name': (s.get('name') or '')}
        if combined:
            item['location'] = r['location']
        stages_bar.append(item)

    try:
        j = int(stage_local_index) if stage_local_index is not None else 0
    except (TypeError, ValueError):
        j = 0
    j = max(0, min(j, count - 1)) if count > 0 else 0

    st_rnd, st_meta = flat_stages[j] if count > 0 else ({}, {})
    settings = normalize_championship(event).get('settings') or {}
    info: dict | None = None
    if count > 0:
        info = {
            'number': j + 1,
            'name': st_meta.get('name') or '',
            'length_km': st_meta.get('distance_km') or 0,
            'weather': st_meta.get('conditions') or '',
            'service': st_meta.get('service_area') or '',
            'surface_deg': st_meta.get('surface_deg') or 'Medium',
            'car_class': st_rnd.get('car_class') or '',
            'location': st_rnd.get('location') or '',
            'hardcore_damage': bool(settings.get('hardcore_damage')),
        }

    # Split columns for the selected stage: rally stages follow the game's
    # max-time limit (15 min -> 4 sectors, 30 min -> 8), rallycross shows one
    # column per lap of the leg's stage plan.
    is_rx = bool(count > 0 and st_rnd.get('location') in RX_LOCATIONS)
    split_count: int | None = None
    if count > 0:
        if is_rx:
            legs = normalize_championship(event).get('events') or []
            leg = legs[st_rnd['index']] if st_rnd['index'] < len(legs) else {}
            plan = rallycross_stage_plan(len(st_rnd.get('stages') or []),
                                         laps=leg.get('rx_laps'))
            local_idx = j - (st_rnd['offset'] if combined else 0)
            split_count = plan[local_idx][1] if 0 <= local_idx < len(plan) else None
        else:
            tid = st_meta.get('track_id')
            limit = stage_limit_ms(tid) if tid else 0
            split_count = split_count_for_limit(limit) if limit else None

    if info is not None and is_rx and split_count:
        info['laps'] = split_count

    entries = entries_for_event(event['id'])
    entries_by_user = {e.get('username'): e for e in entries}
    runs_by_user = _stage_runs_by_user(event['id'])

    raw: list[dict] = []
    if count > 0 and mode == 'stage':
        flat = offset + j
        for e in entries:
            seg = e.get('stages') or []
            se = seg[flat] if flat < len(seg) else None
            t = int((se or {}).get('time_ms', 0) or 0)
            rs = int((se or {}).get('race_status', 0) or 0)
            if t <= 0 and rs == 0:
                continue
            run_obj = runs_by_user.get(e.get('username'), {}).get(flat)
            a = _run_attrs(run_obj)
            # Pre-stage_runs history has no row for the slot — fall back to
            # the compound captured at stage begin (same as Overall mode).
            if a['tyre'] is None and isinstance(se, dict):
                try:
                    a['tyre'] = int(se.get('tyre_compound') or 0) or None
                except (TypeError, ValueError):
                    a['tyre'] = None
            vid = (getattr(run_obj, 'vehicle_id', None) if run_obj else None) \
                or entries_by_user.get(e.get('username'), {}).get('vehicle_id')
            dmg = se.get('comp_damage') if isinstance(se, dict) else None
            if not isinstance(dmg, dict):
                dmg = None
            # Starting state: the snapshot stored at the end of the previous
            # stage (null on the first stage or when never captured — the
            # popup renders missing starts as zero). Season boards are
            # per-round, so a round's first stage starts from zero even
            # though flat > 0.
            prev = seg[flat - 1] if j > 0 and flat - 1 < len(seg) else None
            d0 = prev.get('comp_damage') if isinstance(prev, dict) else None
            if not isinstance(d0, dict):
                d0 = None
            # A set fitted at the service before this stage starts with fresh
            # per-tyre state (wear + impact zeroed; the rest of the car keeps
            # its carried-over damage).
            if d0 is not None and tyre_set_changed(prev, se):
                d0 = fresh_tyre_start(d0)
            raw.append({
                'username': e.get('username'),
                'time_ms': t,
                'penalty_ms': _penalty_ms(se),
                'score': t + _penalty_ms(se),
                'dnf': rs != 0,
                'vid': vid,
                'spares': _spare_tyres(se),
                # One entry per tyre image: the state at the end of this
                # stage (stage mode shows exactly one stage) plus the start
                # state carried in from the previous stage.
                'tyre_damage': [{'s': j, 'd': dmg, 'd0': d0}]
                if a['tyre'] is not None else [],
                **a,
            })
    elif count > 0:
        # Overall: cumulative through the selected stage. Finished stages
        # score their real time + penalties, unfinished ones take the stage's
        # bogey — the site's standard championship-total semantics, sliced to
        # the board's first j+1 flat stages (whole event in combined mode).
        bogeys = flat_stage_bogeys_ms(event)[offset:offset + j + 1]
        for e in entries:
            seg = e.get('stages') or []
            ran = False
            done = 0
            penalty = 0
            tyres_seq: list[int] = []
            # Flat index of the LAST stage each physical set was used — its
            # comp_damage snapshot is the state shown for that set's image.
            tyres_last_flat: list[int] = []
            last_rem: int | None = None
            last_comp: int | None = None
            inputs: list = []
            vrs: list = []
            cams: list = []
            assist_flags: list = []
            spares_list: list = []
            user_runs = runs_by_user.get(e.get('username'), {})
            vid = None
            for k in range(j + 1):
                flat_k = offset + k
                se = seg[flat_k] if flat_k < len(seg) else None
                t = int((se or {}).get('time_ms', 0) or 0)
                rs = int((se or {}).get('race_status', 0) or 0)
                if t > 0:
                    ran = True
                if t > 0 and rs == 0:
                    done += 1
                penalty += _penalty_ms(se)
                run_obj = user_runs.get(flat_k)
                rv = getattr(run_obj, 'vehicle_id', None) if run_obj else None
                if rv is not None:
                    vid = rv
                a = _run_attrs(run_obj)
                comp = a['tyre']
                if comp is None and isinstance(se, dict):
                    try:
                        comp = int(se.get('tyre_compound') or 0) or None
                    except (TypeError, ValueError):
                        comp = None
                rem = None
                if isinstance(se, dict):
                    rv = se.get('tyres_remaining')
                    try:
                        rem = int(rv) if rv is not None else None
                    except (TypeError, ValueError):
                        rem = None
                if comp is not None:
                    appended = False
                    if not tyres_seq:
                        # The set fitted for the first stage.
                        tyres_seq.append(comp)
                        appended = True
                    elif comp != last_comp:
                        # A different compound is always a different physical
                        # set — including at leg boundaries, where
                        # tyres_remaining restarts per leg and never signals
                        # the inter-leg service refit.
                        tyres_seq.append(comp)
                        appended = True
                    elif rem is not None and last_rem is not None \
                            and rem < last_rem:
                        # Same compound, but a spare set of it was fitted at
                        # the service before this stage — one image per set,
                        # in running order.
                        tyres_seq.append(comp)
                        appended = True
                    if appended:
                        tyres_last_flat.append(flat_k)
                    else:
                        tyres_last_flat[-1] = flat_k
                    last_comp = comp
                if rem is not None:
                    last_rem = rem
                inputs.append(a['input'])
                vrs.append(a['vr'])
                cams.append(a['camera'])
                assist_flags.append(a['assists'])
                spares_list.append(_spare_tyres(se))
            if not ran:
                continue
            # One entry per set image: the comp_damage state at the end of
            # the last stage that physical set was used (null when no snapshot
            # was stored for it) plus the start state from the stage before.
            tyre_damage = []
            for fk in tyres_last_flat:
                se_k = seg[fk] if fk < len(seg) else None
                d = se_k.get('comp_damage') if isinstance(se_k, dict) else None
                if not isinstance(d, dict):
                    d = None
                # Look back only within the board's scope: in season mode the
                # first stage of a round must start from zero, not from the
                # previous round's last stage (combined multi has offset=0).
                prev = seg[fk - 1] if fk - offset > 0 and fk - 1 < len(seg) else None
                d0 = prev.get('comp_damage') if isinstance(prev, dict) else None
                if not isinstance(d0, dict):
                    d0 = None
                # A set fitted at the service before this stage starts with
                # fresh per-tyre state (wear + impact zeroed; the rest of the
                # car keeps its carried-over damage).
                if d0 is not None and tyre_set_changed(prev, se_k):
                    d0 = fresh_tyre_start(d0)
                tyre_damage.append({'s': max(0, fk - offset), 'd': d, 'd0': d0})
            total = adjusted_total_ms(e, bogeys, offset=offset)
            raw.append({
                'username': e.get('username'),
                'time_ms': total,
                'penalty_ms': penalty,
                'score': total,
                'dnf': done < j + 1,
                'vid': vid or entries_by_user.get(e.get('username'), {}).get('vehicle_id'),
                'tyres': tyres_seq,
                'tyre_damage': tyre_damage,
                'spares': _most_common(spares_list),
                'input': _most_common(inputs),
                'vr': _most_common(vrs),
                'camera': _most_common(cams),
                'assists': _most_common(assist_flags),
                'splits': None,
            })

    raw.sort(key=lambda r: (r['dnf'], r['score']))
    leader = next((r['score'] for r in raw if not r['dnf']), None)

    # Driver flags (self-declared country) for the board rows.
    flag_by_user: dict = {}
    wanted = {r.get('username') for r in raw if r.get('username')}
    if wanted:
        for u in User.query.filter(User.username.in_(wanted)).all():
            flag_by_user[u.username] = flag_url(u.country or '')

    rows: list[dict] = []
    for i, r in enumerate(raw):
        vid = r.get('vid')
        name, _cls = vehicle_meta(vid) if vid is not None else (None, '')
        if not name:
            name = car_label(entries_by_user.get(r['username'], {})) or ''
        tyres = r.get('tyres')
        if tyres is None:
            tyres = [r['tyre']] if r.get('tyre') is not None else []
        tyre_damage = r.get('tyre_damage') or []
        rows.append({
            'pos': i + 1,
            'username': r['username'],
            'driver_url': f'/profile/{r["username"]}',
            'flag_url': flag_by_user.get(r['username']) or flag_url(''),
            'vehicle': name,
            'car_url': f'/vehicles?vehicle={vid}' if vid is not None else '',
            'tyres': tyres,
            # Parallel to `tyres`: per-image {s: stage-bar local index,
            # d: comp_damage dict or null}.
            'tyre_damage': tyre_damage,
            'spares': r.get('spares'),
            'input': r.get('input'),
            'vr': r.get('vr'),
            'camera': r.get('camera'),
            'assists': r.get('assists'),
            'penalty_ms': r['penalty_ms'],
            'time_text': format_ms(r['time_ms']),
            'diff_str': ('' if (leader is None or r['dnf'])
                         else gap_text(r['score'] - leader)),
            'dnf': r['dnf'],
            'splits': r.get('splits'),
        })

    if count > 0 and not split_count:
        # Unknown rally limit (legacy stage without a track id): mirror the
        # longest splits actually recorded on this stage, defaulting to the
        # common 4-sector shape.
        split_count = max((len(r['splits']) for r in raw if r.get('splits')), default=4)

    return {
        'event_id': event['id'],
        'round_index': round_index,
        'combined': combined,
        'mode': mode,
        'is_rx': is_rx,
        'split_count': split_count,
        'stage_local_index': j,
        'info': info,
        'stages_bar': stages_bar,
        # Full round list so the client can rebuild the round boxes for this
        # championship without a second request.
        'rounds': [{
            'index': r['index'],
            'location': r['location'],
            'thumb_url': location_thumb_url(r['location']),
            'car_class': r['car_class'],
            'class_thumb_url': class_thumb_url(r['car_class']),
            'window_text': r['window_text'],
            'window_start_iso': r.get('window_start_iso'),
            'window_end_iso': r.get('window_end_iso'),
            'status': r['status'],
        } for r in rounds],
        'rows': rows,
    }
