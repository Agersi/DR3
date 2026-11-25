"""Site-wide personal-best leaderboards.

Backed by best_times — one row per driver + car + stage + conditions +
compound, updated by every run type (club stages, official dailies/weeklies/
monthlies, time trials) — with the run_id pointer into stage_runs supplying
the producing run's metadata (source, input method, camera, degradation
level, VR, assists, hardcore damage).  Club rows additionally resolve spares
and game penalties from the club_results stage entry of the producing run's
event slot.

Distance mode aggregates stage_runs directly (SUM(distance_m) per driver in
one car); it has no stage dimension, so only the car + run-based filters
apply there.
"""
from __future__ import annotations

import json

from .countries import flag_url
from .results_view import (
    _penalty_ms,
    _spare_tyres,
    format_ms,
    gap_text,
)
from .scoring import fresh_damage_indices, vehicle_meta
from .tables import RX_LOCATIONS
from dr2server.game_data import STAGE_CONDITIONS_LABELS, Location, Track
from dr2server.stage_limits import split_count_for_limit, stage_limit_ms

# Tyre compound id -> static image slug (mirrors the club board's TYRE_FILE).
TYRE_FILES: dict[int, str] = {
    1: '1_soft_gravel', 2: '2_medium_gravel', 3: '3_hard_gravel',
    4: '4_wet_tarmac', 6: '6_soft_tarmac', 7: '7_medium_tarmac',
    8: '8_hard_tarmac', 9: '9_winter', 10: '10_snow',
    11: '11_wet_rx', 13: '13_dry_rx',
}

SOURCE_LABELS = {'club': 'Club', 'official': 'Official', 'timetrial': 'Time Trial'}

CAMERA_POSITIONS = ['bonnet', 'bumper', 'chase_close', 'chase_far', 'dash', 'head']
INPUT_METHODS = ['keyboard', 'wheel', 'controller']


def _tyre_name(slug: str) -> str:
    """'1_soft_gravel' -> 'Soft Gravel' (the club board derives it the same way)."""
    name = slug.split('_', 1)[1] if '_' in slug else slug
    name = name.replace('_', ' ').title().replace('Rx', 'RX')
    return name


def stage_options() -> list[tuple[str, list[tuple[int, str]]]]:
    """All catalog stages grouped by location for the selector (builder order)."""
    by_loc: dict[Location, list] = {}
    for t in Track:
        by_loc.setdefault(t.location, []).append(
            (int(t), f'{t.display_name} ({t.length_km:.2f} km)'))
    out: list[tuple[str, list]] = []
    for loc in Location:
        if loc not in by_loc:
            continue
        opts = sorted(by_loc[loc], key=lambda kv: kv[1].lower())
        out.append((loc.display_name, opts))
    return out


def car_options() -> list[tuple[str, list[tuple[int, str]]]]:
    """Catalog cars grouped by class label — the same groups as the car page."""
    from .pages.views import _vehicle_class_groups

    return _vehicle_class_groups()


def compound_options() -> list[tuple[int, str]]:
    """Tyre compound id -> display name for the selector."""
    return [(cid, _tyre_name(slug)) for cid, slug in sorted(TYRE_FILES.items())]


# ---------------------------------------------------------------------------
# Run-based filters (applied to the producing run of a best_times row, or to
# the StageRun rows directly in distance mode)
# ---------------------------------------------------------------------------

def _any_run_filter(*, degrad_level, input_method, camera, vr, assists, hardcore):
    return any(v is not None for v in (degrad_level, input_method, camera,
                                       vr, assists, hardcore))


def _run_matches(run, *, degrad_level, input_method, camera, vr, assists, hardcore):
    """True when the run matches every active run-based filter.  A missing run
    never matches — legacy rows without a run_id are dropped whenever any such
    filter is on."""
    if run is None:
        return False
    if degrad_level is not None and \
            round(run.surface_degradation_level or 0.0, 2) != round(degrad_level, 2):
        return False
    if input_method is not None and run.input_method != input_method:
        return False
    if camera is not None and run.camera_position != camera:
        return False
    if vr is not None and bool(run.vr_enabled) != vr:
        return False
    if assists is not None and bool(run.assists_enabled or run.using_assists) != assists:
        return False
    if hardcore is not None and bool(run.hardcore_damage) != hardcore:
        return False
    return True


def _stage_entries_by_slot(event_ids) -> dict:
    """(event_id, username, flat_stage_index) -> club stage entry for the given
    club events — the spares + penalty lookup source for board rows."""
    from .events import entries_for_event

    out = {}
    for event_id in set(event_ids):
        if not event_id:
            continue
        for entry in entries_for_event(event_id):
            for i, se in enumerate(entry.get('stages') or []):
                if isinstance(se, dict):
                    out[(event_id, entry['username'], i)] = se
    return out


# ---------------------------------------------------------------------------
# Cell formatting — the board table is server-rendered with data-* attributes
# and hydrated by the page's cell helpers (same pattern + markup as the home
# page's "Latest club times" table), so rows here carry raw values only
# ---------------------------------------------------------------------------

def _penalty_text(ms):
    if not ms:
        return ''
    s = ms / 1000
    return '+' + (str(int(s)) if float(s).is_integer() else f'{s:.1f}') + 's'


class SimpleRun:
    """Attribute shim so _run_matches works on raw query tuples too."""

    __slots__ = ('surface_degradation_level', 'input_method', 'camera_position',
                 'vr_enabled', 'assists_enabled', 'using_assists', 'hardcore_damage')

    def __init__(self, level, inp, cam, vr_e, ass_e, ass_u, hc):
        self.surface_degradation_level = level
        self.input_method = inp
        self.camera_position = cam
        self.vr_enabled = vr_e
        self.assists_enabled = ass_e
        self.using_assists = ass_u
        self.hardcore_damage = hc


# ---------------------------------------------------------------------------
# Boards
# ---------------------------------------------------------------------------

def best_times_board(track, vehicle_id, *, vehicle_ids=None, conditions=None,
                     compound=None, degrad_level=None, input_method=None,
                     camera=None, vr=None, assists=None, hardcore=None,
                     country=None) -> dict:
    """Best time per driver on one stage, across all run types.

    With a car selected: one row per driver (their fastest row for that car).
    With vehicle_ids (a class selection): every catalog car of that class —
    rendered like the no-car board (one row per driver + car).  Without any
    car filter: every car on the stage.  Without a conditions/compound filter
    each group's fastest row across conditions and compounds wins; with them
    the board is scoped to that cell.  Run-based filters only see rows whose
    best still points at a stored run — legacy rows without a run_id are
    dropped whenever any such filter is on.
    """
    from .db import db
    from .models import StageRun, TimeTrialEntry, User

    q = db.session.query(TimeTrialEntry).filter(
        TimeTrialEntry.track == str(track)[:8])
    if vehicle_ids is not None:
        q = q.filter(TimeTrialEntry.vehicle_id.in_(list(vehicle_ids)))
    elif vehicle_id is not None:
        q = q.filter(TimeTrialEntry.vehicle_id == int(vehicle_id))
    if conditions is not None:
        q = q.filter(TimeTrialEntry.conditions == str(conditions))
    if compound is not None:
        q = q.filter(TimeTrialEntry.tyre_compound == int(compound))
    entries = q.all()

    users = {u.username: u for u in User.query.all()}
    run_ids = [e.run_id for e in entries if e.run_id]
    runs = {}
    if run_ids:
        runs = {r.id: r for r in
                db.session.query(StageRun).filter(StageRun.id.in_(run_ids)).all()}

    need_run = _any_run_filter(degrad_level=degrad_level, input_method=input_method,
                               camera=camera, vr=vr, assists=assists, hardcore=hardcore)
    # One row per driver + car (a single-car board collapses to per-driver).
    by_group: dict[tuple, TimeTrialEntry] = {}
    for e in entries:
        run = runs.get(e.run_id) if e.run_id else None
        if need_run and not _run_matches(
                run, degrad_level=degrad_level, input_method=input_method,
                camera=camera, vr=vr, assists=assists, hardcore=hardcore):
            continue
        if country is not None:
            u = users.get(e.username)
            if (u.country or '' if u else '') != country:
                continue
        key = (e.username, e.vehicle_id)
        cur = by_group.get(key)
        if cur is None or (e.stage_time_ms or 0) < (cur.stage_time_ms or 0):
            by_group[key] = e

    # Spares + penalties for club rows: stage entry of the producing run's slot.
    slots: dict[tuple, tuple] = {}
    event_ids: set = set()
    for key, e in by_group.items():
        run = runs.get(e.run_id) if e.run_id else None
        if run and run.source_type == 'club' and run.event_id \
                and run.flat_stage_index is not None:
            slots[key] = (run.event_id, run.flat_stage_index)
            event_ids.add(run.event_id)
    stage_entries = _stage_entries_by_slot(event_ids)

    # Tyre-click popup payload: the comp_damage snapshot of the producing
    # run's club slot (null for non-club runs or when never captured — the
    # popup shows its no-data fallback, same as the club board).
    damage_of: dict[tuple, dict] = {}
    # Flat ordinals where damage starts from zero per event (first stage +
    # each rally-season round — rounds are separate in-game events).
    reset_of: dict[str, set[int]] = {}
    if event_ids:
        from .models import ClubStageState, Event

        for st in db.session.query(ClubStageState).filter(
                ClubStageState.event_id.in_(sorted(event_ids)),
                ClubStageState.comp_damage_json.isnot(None)).all():
            damage_of[(st.event_id, st.username, st.flat_stage_index)] = \
                json.loads(st.comp_damage_json)
        for ev in db.session.query(Event).filter(
                Event.id.in_(sorted(event_ids))).all():
            reset_of[ev.id] = fresh_damage_indices(ev.to_dict())

    raw = []
    for key, e in by_group.items():
        name, vid = key
        run = runs.get(e.run_id) if e.run_id else None
        se = None
        slot = slots.get(key)
        d = None
        d0 = None
        if slot:
            se = stage_entries.get((slot[0], name, slot[1]))
            d = damage_of.get((slot[0], name, slot[1]))
            # Start state: the previous flat stage's snapshot (null at a
            # fresh-damage ordinal — event start / season round start — or
            # when never captured; rendered as zero).
            if slot[1] > 0 and slot[1] not in reset_of.get(slot[0], {0}):
                d0 = damage_of.get((slot[0], name, slot[1] - 1))
        u = users.get(name)
        splits = None
        if e.splits_json:
            try:
                loaded = json.loads(e.splits_json)
                splits = loaded if isinstance(loaded, list) and loaded else None
            except (ValueError, TypeError):
                splits = None
        # Only known ids get a label — legacy rows store 0/unknown and the
        # popup would otherwise show the "Conditions #N" fallback.
        try:
            cond_id = int(str(e.conditions))
        except (ValueError, TypeError):
            cond_id = 0
        cond_label = STAGE_CONDITIONS_LABELS.get(cond_id, '') if cond_id else ''
        car_name, car_class = vehicle_meta(vid)
        raw.append({
            'username': name,
            'vehicle_id': vid,
            'car_name': car_name,
            'car_class': car_class,
            'time_ms': e.stage_time_ms or 0,
            'compound': int(e.tyre_compound) if e.tyre_compound else None,
            'spares': _spare_tyres(se),
            'penalty_ms': _penalty_ms(se),
            'input': run.input_method if run else None,
            'vr': bool(run.vr_enabled) if run else None,
            'camera': run.camera_position if run else None,
            'assists': (bool(run.assists_enabled or run.using_assists) if run
                        else (bool(e.using_assists) if e.using_assists is not None else None)),
            'source': SOURCE_LABELS.get(run.source_type) if run else None,
            'flag_url': flag_url(u.country or '') if u else '',
            'splits': splits,
            'tyre_damage': ([{'s': 0, 'd': d, 'd0': d0}]
                            if e.tyre_compound else []),
            'conditions_label': cond_label,
        })
    # Split columns: rally stages follow the game's max-time limit (15 min ->
    # 4 sectors, 30 min -> 8). The RX board is about the best LAP — an RX event
    # can be anything from 2 to 12 laps, so there is no meaningful split shape
    # here and the columns are omitted entirely.
    is_rx = False
    try:
        is_rx = Track(int(track)).location.display_name in RX_LOCATIONS
    except (ValueError, TypeError):
        pass
    split_count: int | None = None
    if is_rx:
        split_count = 0
    else:
        limit = stage_limit_ms(int(track))
        split_count = split_count_for_limit(limit) if limit else None
        if not split_count:
            split_count = max((len(r['splits']) for r in raw if r.get('splits')),
                              default=4)

    raw.sort(key=lambda r: (r['time_ms'], r['username']))
    leader = raw[0]['time_ms'] if raw else None

    rows = []
    for i, r in enumerate(raw):
        u = users.get(r['username'])
        rows.append({
            'pos': i + 1,
            'username': r['username'],
            'vehicle_id': r['vehicle_id'],
            'display_name': (u.display_name or r['username']) if u else r['username'],
            'car_name': r['car_name'],
            'car_class': r['car_class'],
            'flag_url': r['flag_url'],
            'tyres': [r['compound']] if r['compound'] else [],
            'spares': r['spares'],
            'input': r['input'],
            'vr': r['vr'],
            'camera': r['camera'],
            'assists': r['assists'],
            'source': r['source'],
            'penalty_text': _penalty_text(r['penalty_ms']),
            'time_text': format_ms(r['time_ms']),
            'diff_str': gap_text(r['time_ms'] - leader) if leader is not None else '',
            # Padded to split_count (None = no splits recorded for the run).
            'splits': ((r['splits'] or [])[:split_count]
                       + [None] * split_count)[:split_count],
            'tyre_damage': r['tyre_damage'],
            'conditions_label': r['conditions_label'],
        })
    return {'rows': rows, 'count': len(rows), 'split_count': split_count,
            'split_word': 'Lap' if is_rx else 'Split', 'is_rx': is_rx}


def distance_board(vehicle_id, *, vehicle_ids=None, degrad_level=None,
                   input_method=None, camera=None, vr=None, assists=None,
                   hardcore=None, country=None) -> dict:
    """Total distance driven per driver across all run types and stages —
    in one car when selected, over the cars of a class when vehicle_ids is
    given, over every car when neither is set.  Every recorded run counts as
    a stage (same denominator as /drivers); DNF runs carry no trusted
    distance_m and contribute 0 to the distance sum."""
    from .db import db
    from .models import StageRun, User

    users = {u.username: u for u in User.query.all()}
    q = db.session.query(
        StageRun.username, StageRun.distance_m,
        StageRun.surface_degradation_level, StageRun.input_method,
        StageRun.camera_position, StageRun.vr_enabled,
        StageRun.assists_enabled, StageRun.using_assists,
        StageRun.hardcore_damage)
    if vehicle_ids is not None:
        q = q.filter(StageRun.vehicle_id.in_(list(vehicle_ids)))
    elif vehicle_id is not None:
        q = q.filter(StageRun.vehicle_id == int(vehicle_id))
    rows = q.all()

    sums: dict[str, list] = {}  # username -> [meters, stage count]
    for user, dist, level, inp, cam, vr_e, ass_e, ass_u, hc in rows:
        if country is not None:
            u = users.get(user)
            if (u.country or '' if u else '') != country:
                continue
        if not _run_matches(
                SimpleRun(level, inp, cam, vr_e, ass_e, ass_u, hc),
                degrad_level=degrad_level, input_method=input_method,
                camera=camera, vr=vr, assists=assists, hardcore=hardcore):
            continue
        cur = sums.setdefault(user, [0, 0])
        cur[0] += int(dist or 0)
        cur[1] += 1

    ordered = sorted(sums.items(), key=lambda kv: (-kv[1][0], kv[0]))
    out = []
    for i, (name, (meters, stages)) in enumerate(ordered):
        u = users.get(name)
        km = meters / 1000
        out.append({
            'pos': i + 1,
            'username': name,
            'display_name': (u.display_name or name) if u else name,
            'flag_url': flag_url(u.country or '') if u else '',
            'stages': stages,
            'km_text': f'{km:,.1f}',
        })
    return {'rows': out, 'count': len(out)}


def stage_info(track) -> dict:
    """Display metadata for the selected stage (name / location / length)."""
    try:
        t = Track(int(track))
    except (ValueError, TypeError):
        return {}
    return {
        'name': t.display_name,
        'location': t.location.display_name if t.location else '',
        'length_km': t.length_km,
    }
