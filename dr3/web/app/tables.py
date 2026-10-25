"""Game data tables derived from dr2server.game_data (single source of truth)."""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dr2server.game_data import (  # noqa: E402
    Location,
    Track,
    SURFACE_DEGRAD_LEVELS,
    SERVICE_AREA_LEVELS,
    CONFIRMED_VEHICLE_CLASS_IDS,
    STAGE_CONDITIONS_LABELS,
    get_tracks_for_location,
    get_verified_routes_for_location,
    nearest_stage_conditions_for_location,
    stage_conditions_for_location,
    stage_conditions_label,
    stage_conditions_options_for_location,
    vehicle_class_id_for_label,
)

STAGES: dict[str, list[tuple[str, float]]] = {
    loc.display_name: [
        (t.display_name, t.length_km)
        for t in Track
        if t.location is loc
    ]
    for loc in Location
    if any(t.location is loc for t in Track)
}

RX_LOCATIONS: frozenset[str] = frozenset(
    loc.display_name for loc in Location if loc.discipline == 'rallycross'
)

VERIFIED_STAGE_COUNTS: dict[str, int] = {
    loc.display_name: len(get_tracks_for_location(int(loc)))
    for loc in Location
    if loc.display_name in STAGES
}

STAGE_CAPS: dict[str, int] = {
    name: (count or len(STAGES[name]))
    for name, count in VERIFIED_STAGE_COUNTS.items()
}

STAGE_ROUTES: dict[str, list[tuple[int, str, float]]] = {
    loc.display_name: get_verified_routes_for_location(int(loc))
    for loc in Location
    if loc.display_name in STAGES
}

SURFACE_DEG_OPTIONS: list[str] = [label for label, _ in SURFACE_DEGRAD_LEVELS]
SERVICE_AREA_OPTIONS: list[str] = [label for label, _has, _sid in SERVICE_AREA_LEVELS]

CAR_CLASSES: dict[str, list[str]] = {
    'H1 (FWD)': ['Mini Cooper S', 'DS Automobiles DS 21', 'Lancia Fulvia HF'],
    'H2 (FWD)': ['Volkswagen Golf GTI 16V', 'Peugeot 205 GTI'],
    'H3 (RWD)': [
        'BMW E30 M3 Evo Rally', 'Opel Ascona 400', 'Lancia Stratos',
        'Datsun 240Z', 'Renault 5 Turbo', 'Ford Sierra Cosworth RS500',
    ],
    'R2': ['Ford Fiesta R2', 'Opel Adam R2', 'Peugeot 208 R2'],
    'Group A': [
        'Subaru Impreza 1995', 'Mitsubishi Lancer Evo VI',
        'Ford Escort RS Cosworth', 'Subaru Legacy RS',
    ],
    'Group B (4WD)': [
        'Audi Sport quattro S1 E2', 'Peugeot 205 T16 Evo 2',
        'Lancia Delta S4', 'Ford RS200', 'MG Metro 6R4',
    ],
    'Group B (RWD)': ['Lancia 037 Evo 2', 'Opel Manta 400', 'BMW M1 Procar Rally'],
    'R5': [
        'Ford Fiesta R5', 'Volkswagen Polo GTI R5',
        'Citroen C3 R5', 'Skoda Fabia R5', 'Peugeot 208 T16 R5',
    ],
    'NR4/R4': ['Subaru WRX STI NR4', 'Mitsubishi Lancer Evo X'],
    'H2 (RWD)': ['Porsche 911 SC RS', 'Fiat 131 Abarth Rally', 'Opel Kadett C GT/E'],
    'Rally GT': [
        'Porsche 911 RGT Rally Spec', 'BMW M2 Competition',
        'Chevrolet Camaro GT4.R', 'Aston Martin V8 Vantage GT4',
    ],
    'F2 Kit Car': ['Peugeot 306 Maxi', 'Seat Ibiza Kit Car', 'Volkswagen Golf Kitcar'],
    '2000cc': [
        'Citroen C4 Rally', 'Skoda Fabia Rally',
        'Ford Focus RS Rally 2007', 'Subaru Impreza 2008',
    ],
}

# Rallycross classes — offered only for rallycross locations.  Labels are the
# VehicleClass labels from dr2server.game_data (RX ids confirmed: upstream
# RaceNet's own RX challenges carry e.g. Requirement Type=1 Value=78).
RX_CAR_CLASSES: dict[str, list[str]] = {
    'RX Super 1600': [
        'Volkswagen Polo S1600', 'Renault Sport Clio R.S. S1600',
        'Opel Corsa Super 1600',
    ],
    'Cross Kart': ['SpeedCar Xtrem'],
    'Group B Rallycross': [
        'Lancia Delta S4 Rallycross', 'Ford RS200 Evolution',
        'Peugeot 205 T16 Rallycross', 'MG Metro 6R4 Rallycross',
    ],
    'RX2': ['Ford Fiesta OMSE Supercar Lites'],
    'RX Supercars': [
        'Volkswagen Polo R Supercar', 'Audi S1 EKS RX Quattro',
        'Peugeot 208 WRX', 'Renault Megane RS RX',
        'Ford Fiesta Rallycross Mk8', 'Ford Fiesta Rallycross Mk7',
        'Subaru WRX STI Rallycross',
    ],
    'RX Supercars 2019': [
        'Renault Megane R.S. RX', 'Peugeot 208 WRX', 'Audi S1 EKS RX Quattro',
        'Renault Clio R.S. RX', 'Ford Fiesta RXS Evo 5', 'Ford Fiesta RX (Mk8)',
        'Mini Cooper SX1', 'Ford Fiesta Rallycross (Stard)', 'Seat Ibioza RX',
    ],
}


# Every class is selectable for every event, rally or rallycross — players may
# run RX cars on rally stages and rally cars on RX circuits.
ALL_CAR_CLASSES: dict[str, list[str]] = {**CAR_CLASSES, **RX_CAR_CLASSES}

CONDITIONS_BY_LOCATION: dict[str, list[tuple[int, str]]] = {
    loc: stage_conditions_options_for_location(loc) for loc in STAGES
}

COUNTRIES: dict[str, str] = {
    'Argentina': '\U0001F1E6\U0001F1F7',
    'Australia': '\U0001F1E6\U0001F1FA',
    'Austria': '\U0001F1E6\U0001F1F9',
    'Belgium': '\U0001F1E7\U0001F1EA',
    'Brazil': '\U0001F1E7\U0001F1F7',
    'Bulgaria': '\U0001F1E7\U0001F1EC',
    'Canada': '\U0001F1E8\U0001F1E6',
    'Chile': '\U0001F1E8\U0001F1F1',
    'China': '\U0001F1E8\U0001F1F3',
    'Colombia': '\U0001F1E8\U0001F1F4',
    'Croatia': '\U0001F1ED\U0001F1F7',
    'Czech Republic': '\U0001F1E8\U0001F1FF',
    'Denmark': '\U0001F1E9\U0001F1F0',
    'England': '\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F',
    'Estonia': '\U0001F1EA\U0001F1EA',
    'Finland': '\U0001F1EB\U0001F1EE',
    'France': '\U0001F1EB\U0001F1F7',
    'Germany': '\U0001F1E9\U0001F1EA',
    'Greece': '\U0001F1EC\U0001F1F7',
    'Hungary': '\U0001F1ED\U0001F1FA',
    'Iceland': '\U0001F1EE\U0001F1F8',
    'India': '\U0001F1EE\U0001F1F3',
    'Indonesia': '\U0001F1EE\U0001F1E9',
    'Ireland': '\U0001F1EE\U0001F1EA',
    'Israel': '\U0001F1EE\U0001F1F1',
    'Italy': '\U0001F1EE\U0001F1F9',
    'Japan': '\U0001F1EF\U0001F1F5',
    'Kenya': '\U0001F1F0\U0001F1EA',
    'Latvia': '\U0001F1F1\U0001F1FB',
    'Lithuania': '\U0001F1F1\U0001F1F9',
    'Luxembourg': '\U0001F1F1\U0001F1FA',
    'Malaysia': '\U0001F1F2\U0001F1FE',
    'Mexico': '\U0001F1F2\U0001F1FD',
    'Monaco': '\U0001F1F2\U0001F1E8',
    'Netherlands': '\U0001F1F3\U0001F1F1',
    'New Zealand': '\U0001F1F3\U0001F1FF',
    'Northern Ireland': '\U0001F1EC\U0001F1E7',
    'Norway': '\U0001F1F3\U0001F1F4',
    'Peru': '\U0001F1F5\U0001F1EA',
    'Philippines': '\U0001F1F5\U0001F1ED',
    'Poland': '\U0001F1F5\U0001F1F1',
    'Portugal': '\U0001F1F5\U0001F1F9',
    'Romania': '\U0001F1F7\U0001F1F4',
    'Russia': '\U0001F1F7\U0001F1FA',
    'Scotland': '\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F',
    'Serbia': '\U0001F1F7\U0001F1F8',
    'Singapore': '\U0001F1F8\U0001F1EC',
    'Slovakia': '\U0001F1F8\U0001F1F0',
    'Slovenia': '\U0001F1F8\U0001F1EE',
    'South Africa': '\U0001F1FF\U0001F1E6',
    'South Korea': '\U0001F1F0\U0001F1F7',
    'Spain': '\U0001F1EA\U0001F1F8',
    'Sweden': '\U0001F1F8\U0001F1EA',
    'Switzerland': '\U0001F1E8\U0001F1ED',
    'Thailand': '\U0001F1F9\U0001F1ED',
    'Turkey': '\U0001F1F9\U0001F1F7',
    'Ukraine': '\U0001F1FA\U0001F1E6',
    'United Arab Emirates': '\U0001F1E6\U0001F1EA',
    'United Kingdom': '\U0001F1EC\U0001F1E7',
    'United States': '\U0001F1FA\U0001F1F8',
    'Uruguay': '\U0001F1FA\U0001F1FE',
    'Vietnam': '\U0001F1FB\U0001F1F3',
    'Wales': '\U0001F3F4\U000E0067\U000E0062\U000E0077\U000E006C\U000E0073\U000E007F',
}

LOCATION_SURFACE: dict[str, str] = {
    'Monte Carlo': 'Tarmac',
    'Spain': 'Tarmac',
    'Barcelona': 'Tarmac/Gravel',
    'Hell': 'Tarmac/Gravel',
    'Höljes': 'Tarmac/Gravel',
    'Loheac': 'Tarmac/Gravel',
    'Lydden Hill': 'Tarmac/Gravel',
    'Mettet': 'Tarmac/Gravel',
    'Montalegre': 'Tarmac/Gravel',
    'Estering': 'Tarmac/Gravel',
    'Bikernieki': 'Tarmac/Gravel',
    'Killarney': 'Tarmac/Gravel',
    'Silverstone': 'Tarmac',
    'Trois-Rivières': 'Tarmac/Gravel',
    'Yas Marina': 'Tarmac',
}

DURATION_OPTIONS: dict[str, tuple[str, timedelta]] = {
    '24h': ('daily', timedelta(hours=24)),
    '1week': ('weekly', timedelta(weeks=1)),
    '1month': ('monthly', timedelta(days=30)),
}

MAX_EVENT_DAYS = 14
MIN_CHAMP_DURATION = timedelta(hours=1)
MAX_CHAMP_DURATION = timedelta(days=180)

# Defaults for a championship's settings_json.  'scoring' picks the per-rally
# points system (see SCORING_* in app/scoring.py) and 'power_stage' toggles
# the final-stage bonus; both are chosen in the builder and stored with the
# event, so old events without them normalize to WRC / off.
DEFAULT_CHAMP_SETTINGS: dict = {
    'hardcore_damage': True,
    'force_cockpit_camera': False,
    'allow_assists': True,
    'unexpected_moments': True,
    'scoring': 'wrc',
    'power_stage': False,
}

# Championship modes.  'multi' (Multi-Location Rally): every rally is
# available at once inside ONE shared window — only the first event's duration
# is set and it spans the whole championship.  'season' (Rally Season): each
# rally has its own duration and the windows are staggered back-to-back, so a
# rally only opens when the previous one's window ends.
CHAMP_MODE_MULTI = 'multi'
CHAMP_MODE_SEASON = 'season'
CHAMP_MODES: tuple[str, str] = (CHAMP_MODE_MULTI, CHAMP_MODE_SEASON)


def _event_timedelta(d: dict) -> timedelta:
    try:
        days = int(d.get('days', 0) or 0)
        hours = int(d.get('hours', 0) or 0)
        mins = int(d.get('mins', 0) or 0)
    except (TypeError, ValueError):
        return timedelta(0)
    return timedelta(days=days, hours=hours, minutes=mins)


def leg_duration(ev: dict) -> timedelta:
    """One championship sub-event's stored {days, hours, mins} duration."""
    return _event_timedelta(ev.get('duration', {}))


def championship_duration(events: list[dict]) -> timedelta:
    total = timedelta(0)
    for ev in events:
        total += _event_timedelta(ev.get('duration', {}))
    return total


def validate_event_duration(d: dict) -> list[str]:
    errors: list[str] = []
    for key in ('days', 'hours', 'mins'):
        try:
            v = int(d.get(key, 0) or 0)
        except (TypeError, ValueError):
            errors.append(f'Invalid {key} in event duration.')
            continue
        if v < 0:
            errors.append(f'Event {key} cannot be negative.')
        if key == 'days' and v > MAX_EVENT_DAYS:
            errors.append(f'An event can run at most {MAX_EVENT_DAYS} days.')
    return errors


def bucket_for_duration(total: timedelta) -> str:
    if total <= timedelta(hours=24):
        return 'daily'
    if total <= timedelta(days=7):
        return 'weekly'
    return 'monthly'


def duration_for_type(event_type: str) -> dict[str, int]:
    key = {'daily': '24h', 'weekly': '1week', 'monthly': '1month'}.get(event_type, '1week')
    _t, delta = DURATION_OPTIONS[key]
    total_min = int(delta.total_seconds() // 60)
    days, rem = divmod(total_min, 24 * 60)
    hours, mins = divmod(rem, 60)
    return {'days': days, 'hours': hours, 'mins': mins}


def normalize_championship(raw: dict) -> dict:
    """Return a canonical v2 championship dict (events[] + settings).

    Accepts both the multi-event shape and the legacy single-event shape so
    every consumer can treat stored events uniformly. Never mutates input.
    """
    champ = dict(raw)
    settings = dict(DEFAULT_CHAMP_SETTINGS)
    settings.update(champ.get('settings') or {})
    champ['settings'] = settings
    mode = settings.get('mode')
    champ['mode'] = mode if mode in CHAMP_MODES else CHAMP_MODE_MULTI

    events = champ.get('events')
    if not events:
        events = [{
            'location': champ.get('location', ''),
            'car_class': champ.get('car_class', ''),
            'surface': champ.get('surface', 'Gravel'),
            'duration': duration_for_type(champ.get('type', 'weekly')),
            'stages': champ.get('stages', []),
        }]

    norm_events: list[dict] = []
    for ev in events:
        leg = dict(ev)
        leg.setdefault('surface', champ.get('surface', 'Gravel'))
        leg.setdefault('duration', duration_for_type(champ.get('type', 'weekly')))
        leg.setdefault('stages', [])
        norm_events.append(leg)
    champ['events'] = norm_events
    return champ


# ── Championship builder: blanks, random fill, form parsing ─────────

MAX_CHAMP_EVENTS = 12
MAX_STAGES_PER_EVENT = 12

# Rallycross lap option: per-event laps per stage, applied to every stage of
# an RX event.  Must stay >= 1 (0 is the rally stage shape and crashes the
# game on load); capped so a stage stays a reasonable length.
MIN_RX_LAPS = 1
MAX_RX_LAPS = 12

import random as _random  # noqa: E402
import re as _re  # noqa: E402


def service_area_for_pos(pos: int) -> str:
    return 'Medium' if pos % 2 == 0 else 'None'


def blank_stage(pos: int = 0) -> dict:
    return {'track_id': None, 'conditions_id': None,
            'surface_deg': 'Medium', 'service_area': service_area_for_pos(pos)}


def blank_event(num_stages: int = 1) -> dict:
    return {
        'location': '',
        'car_class': '',
        'duration': {'days': 2, 'hours': 0, 'mins': 0},
        'rx_laps': None,
        'stages': [blank_stage(i) for i in range(max(1, num_stages))],
    }


_RANDOM_SURFACE_DEG = ['Medium', 'High', 'Max']


def _condition_weight(label: str) -> float:
    parts = [p.strip() for p in label.split('/')]
    tod = parts[0] if parts else ''
    wet = parts[2] if len(parts) > 2 else ''
    weight = 1.0
    if tod == 'Daytime':
        weight *= 4.0
    elif tod in ('Sunset', 'Dusk'):
        weight *= 1.5
    if wet == 'Dry':
        weight *= 3.0
    return weight


def _random_condition_for(rng, location: str):
    ids = stage_conditions_for_location(location)
    if not ids:
        return None
    weights = [_condition_weight(stage_conditions_label(cid)) for cid in ids]
    return rng.choices(ids, weights=weights, k=1)[0]


def random_championship_events(num_events: int, num_stages: int) -> list[dict]:
    """Semi-randomly fill events (RaceNet-style) so the editor opens pre-populated."""
    rng = _random.Random()
    loc_pool = [
        l for l in STAGES
        if VERIFIED_STAGE_COUNTS.get(l, 0) > 0 and l not in RX_LOCATIONS
    ]
    class_pool = [
        c for c in CAR_CLASSES
        if (vehicle_class_id_for_label(c) or 0) in CONFIRMED_VEHICLE_CLASS_IDS
    ]
    car_class = rng.choice(class_pool) if class_pool else ''

    used_locs: set[str] = set()
    events: list[dict] = []
    for _ in range(num_events):
        avail = [l for l in loc_pool if l not in used_locs] or loc_pool
        rich = [l for l in avail if VERIFIED_STAGE_COUNTS.get(l, 0) >= num_stages]
        location = rng.choice(rich or avail) if avail else ''
        used_locs.add(location)

        routes = list(STAGE_ROUTES.get(location, []))
        rng.shuffle(routes)
        picked = routes[:num_stages]
        stages = [
            {
                'track_id': tid,
                'conditions_id': _random_condition_for(rng, location),
                'surface_deg': rng.choice(_RANDOM_SURFACE_DEG),
                'service_area': service_area_for_pos(i),
            }
            for i, (tid, _name, _km) in enumerate(picked)
        ] or [blank_stage(i) for i in range(num_stages)]

        events.append({
            'location': location,
            'car_class': car_class,
            'duration': {'days': 2, 'hours': 0, 'mins': 0},
            'rx_laps': None,
            'stages': stages,
        })
    return events


def to_int_or_none(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def to_int_or_zero(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


_CHAMP_FIELD_RE = _re.compile(r'^events\[(\d+)\](?:\[stages\]\[(\d+)\])?\[(\w+)\]$')


def parse_championship_form(form) -> dict:
    """Reconstruct {name, events:[...]} from bracketed indexed form fields."""
    events: dict[int, dict] = {}
    for key in form.keys():
        m = _CHAMP_FIELD_RE.match(key)
        if not m:
            continue
        ei = int(m.group(1))
        sj = m.group(2)
        field = m.group(3)
        ev = events.setdefault(ei, {'fields': {}, 'stages': {}})
        if sj is None:
            ev['fields'][field] = form.get(key, '')
        else:
            ev['stages'].setdefault(int(sj), {})[field] = form.get(key, '')

    out_events: list[dict] = []
    for ei in sorted(events):
        raw = events[ei]
        f = raw['fields']
        location = (f.get('location') or '').strip()
        valid_conds = stage_conditions_for_location(location)
        stages_out: list[dict] = []
        for sj in sorted(raw['stages']):
            s = raw['stages'][sj]
            cond_id = to_int_or_none(s.get('conditions'))
            if valid_conds and cond_id not in valid_conds:
                cond_id = (nearest_stage_conditions_for_location(location, cond_id)
                           or valid_conds[0])
            stages_out.append({
                'track_id': to_int_or_none(s.get('route')),
                'conditions_id': cond_id,
                'surface_deg': (s.get('surface_deg') or 'Medium').strip(),
                'service_area': (s.get('service_area') or 'Medium').strip(),
            })
        out_events.append({
            'location': location,
            'car_class': (f.get('car_class') or '').strip(),
            'duration': {
                'days': to_int_or_zero(f.get('duration_days')),
                'hours': to_int_or_zero(f.get('duration_hours')),
                'mins': to_int_or_zero(f.get('duration_mins')),
            },
            'rx_laps': to_int_or_none(f.get('rx_laps')),
            'stages': stages_out or [blank_stage()],
        })
    return {'name': (form.get('name') or '').strip(), 'events': out_events}


def enrich_stage(location: str, s: dict) -> dict:
    routes = {tid: (name, km) for tid, name, km in STAGE_ROUTES.get(location, [])}
    tid = s.get('track_id')
    name, km = routes.get(tid, ('', 0.0)) if tid is not None else ('', 0.0)
    cid = s.get('conditions_id')
    label = STAGE_CONDITIONS_LABELS.get(cid, '') if cid is not None else ''
    return {
        'name': name, 'track_id': tid, 'distance_km': km,
        'conditions_id': cid, 'conditions': label,
        'surface_deg': s.get('surface_deg', 'Medium'),
        'service_area': s.get('service_area', 'Medium'),
    }


def validate_championship(events: list[dict], mode: str = CHAMP_MODE_MULTI) -> list[str]:
    errors: list[str] = []
    if not events:
        return ['A championship needs at least one event.']
    classes: set[str] = set()
    for i, ev in enumerate(events, start=1):
        loc = ev.get('location', '')
        cls = ev.get('car_class', '')
        classes.add(cls)
        if loc not in STAGES:
            errors.append(f'Event {i}: invalid location.')
        # Any confirmed class is valid on any discipline — RX cars may be used
        # on rally stages and rally cars on RX circuits.
        vclass_id = vehicle_class_id_for_label(cls)
        if cls not in ALL_CAR_CLASSES or vclass_id is None \
                or vclass_id not in CONFIRMED_VEHICLE_CLASS_IDS:
            errors.append(f'Event {i}: invalid or unsupported vehicle class.')
        errors.extend(f'Event {i}: {e}' for e in validate_event_duration(ev.get('duration', {})))
        # In a Rally Season every rally owns its own staggered window, so each
        # one needs a real duration.  In a Multi-Location Rally only the
        # first event's duration matters (the shared window) — later events'
        # stored durations are forced to zero and may stay so.
        if mode == CHAMP_MODE_SEASON and leg_duration(ev) <= timedelta(0):
            errors.append(f'Event {i}: duration must be greater than zero.')
        if loc in RX_LOCATIONS:
            laps = ev.get('rx_laps')
            if laps is not None and not (MIN_RX_LAPS <= int(laps) <= MAX_RX_LAPS):
                errors.append(
                    f'Event {i}: laps must be between {MIN_RX_LAPS} and {MAX_RX_LAPS}.')
        stages = ev.get('stages', [])
        cap = STAGE_CAPS.get(loc, len(STAGES.get(loc, [])))
        if not (1 <= len(stages) <= MAX_STAGES_PER_EVENT):
            errors.append(f'Event {i}: must have 1-{MAX_STAGES_PER_EVENT} stages.')
        # The cap is the location's verified ROUTE count — it stops a rally
        # event from picking unverified stages.  A rallycross event has one
        # verified circuit and runs several lapped stages ON that circuit
        # (RaceNet's own knockout format), so the route count must not cap it.
        if loc in STAGES and cap and len(stages) > cap and loc not in RX_LOCATIONS:
            errors.append(f'Event {i}: {loc} supports at most {cap} stages.')
        valid_track_ids = {tid for tid, _n, _km in STAGE_ROUTES.get(loc, [])}
        valid_conditions = stage_conditions_for_location(loc)
        for sj, s in enumerate(stages, start=1):
            tid = s.get('track_id')
            if tid is None or tid not in valid_track_ids:
                errors.append(f'Event {i} stage {sj}: pick a route.')
            cid = s.get('conditions_id')
            if cid is None or cid not in valid_conditions:
                errors.append(f'Event {i} stage {sj}: pick conditions.')
            if s.get('surface_deg') not in SURFACE_DEG_OPTIONS:
                errors.append(f'Event {i} stage {sj}: invalid surface degradation.')
            if s.get('service_area') not in SERVICE_AREA_OPTIONS:
                errors.append(f'Event {i} stage {sj}: invalid service area.')
    # Multi-Location Rally: one class across all events — car damage and tyre
    # state carry over between rallies, so a mid-championship class change is
    # untested.  Rally Season: each rally is an independent window and may use
    # its own class.
    if mode == CHAMP_MODE_MULTI and len(classes - {''}) > 1:
        errors.append('All events must use the same vehicle class.')
    return errors
