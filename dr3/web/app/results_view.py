"""View helpers for championship results on the club/event detail pages.

Both pages share these: per-round (leg) windows and statuses, the overall
standings with points, the per-round classification, and stage-by-stage
result grids.  All data comes from the Result rows the game endpoints store;
scoring reuses ``scoring.py`` so the site shows exactly what the in-game
leaderboard computes.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .scoring import (
    car_label,
    championship_points,
    championship_view,
    overall_standings,
    rally_standings,
    scoring_system_for,
)
from .tables import (
    CHAMP_MODE_SEASON,
    RX_LOCATIONS,
    drivetrains_for_class,
    enrich_stage,
    leg_duration,
    normalize_championship,
)

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

    Drivetrain comes from the active leg's car class — seasons switch legs,
    Multi-Location rallies run one class throughout (the first leg's)."""
    if not event:
        return None
    champ = normalize_championship(event)
    settings = champ['settings']
    legs = champ['events']
    idx = current_leg_index(event, now) if champ['mode'] == CHAMP_MODE_SEASON else 0
    car_class = (legs[idx].get('car_class') or '') if legs else ''
    return {
        'mode': champ['mode'],
        'hardcore_damage': bool(settings.get('hardcore_damage')),
        'external_cameras': not bool(settings.get('force_cockpit_camera')),
        'allow_assists': bool(settings.get('allow_assists')),
        'car_class': car_class,
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
