"""Per-car page data (website).

CARS maps each catalog vehicle id (dr2server.game_data.Vehicle) to its
cars.json entry (static/cars/cars.json — user-supplied specs + image path),
matched per class with the same optimal name matching as
tools/verify_cars_json.py (exact > subset > fuzzy). Fastest-stage
leaderboards come from best_times (the site leaderboard's source);
distance and terminal-damage figures from stage_runs — both computed at
request time.
"""
from __future__ import annotations

import difflib
import itertools
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dr2server.game_data import _VEHICLE_META  # noqa: E402

_CAR_DIR = Path(__file__).resolve().parent.parent / 'static' / 'cars'
_CARS_JSON = _CAR_DIR / 'cars.json'
_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}


def _norm_tokens(s: str) -> list[str]:
    s = unicodedata.normalize('NFKD', s or '')
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^a-z0-9]+', ' ', s.lower())
    return [t for t in s.split() if t]


def _entry_score(entry: dict, vid) -> float:
    """Name similarity of a cars.json entry to a catalog vehicle (0..1)."""
    name = f"{entry.get('brand', '')} {entry.get('model', '')}"
    etoks = set(_norm_tokens(name))
    dtoks = set(_norm_tokens(_VEHICLE_META[vid]['display_name']))
    if etoks == dtoks:
        return 1.0
    if (dtoks < etoks and len(dtoks) >= 2) or (etoks < dtoks and len(etoks) >= 2):
        return 0.9
    ename = ' '.join(sorted(etoks))
    dname = ' '.join(sorted(dtoks))
    return difflib.SequenceMatcher(None, ename, dname).ratio() * 0.85


def _load_cars() -> dict[int, dict]:
    """cars.json entries keyed by catalog vehicle id (int).

    Per-class optimal name matching (exact > subset > fuzzy) — the same
    algorithm as tools/verify_cars_json.py, so duplicate names across
    classes (e.g. the 2019 RX Supercars) resolve to their own class and
    near-names (Alpine A110, Clio S1600, Fiesta RX Mk8) still match.
    """
    from dr2server.game_data import Vehicle

    if not _CARS_JSON.is_file():
        return {}
    try:
        entries = json.loads(_CARS_JSON.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}

    by_class: dict[str, list] = {}
    for vid in Vehicle:
        by_class.setdefault(_VEHICLE_META[vid]['vehicle_class'].label, []).append(vid)

    class_entries: dict[str, list[int]] = {}
    for i, e in enumerate(entries):
        class_entries.setdefault(str(e.get('class', '')), []).append(i)

    assignment: dict[int, int] = {}  # entry index -> vehicle id
    for cls, idxs in class_entries.items():
        cands = by_class.get(cls, [])
        n = min(len(idxs), len(cands))
        if n == 0:
            continue
        if max(len(idxs), len(cands)) > 8:
            order = sorted(
                ((_entry_score(entries[i], v), i, v) for i in idxs for v in cands),
                reverse=True)
            used_i, used_v = set(), set()
            for _score, i, v in order:
                if i in used_i or v in used_v:
                    continue
                assignment[i] = int(v)
                used_i.add(i)
                used_v.add(v)
        else:
            scores = [[_entry_score(entries[i], v) for v in cands] for i in idxs]
            best_perm, best_total = None, -1.0
            for perm in itertools.permutations(range(len(cands)), n):
                total = sum(scores[k][perm[k]] for k in range(n))
                if total > best_total:
                    best_total, best_perm = total, perm
            for k in range(n):
                assignment[idxs[k]] = int(cands[best_perm[k]])

    return {vid: entries[i] for i, vid in assignment.items()}


CARS: dict[int, dict] = _load_cars()

_CAR_FILES = (
    {p.relative_to(_CAR_DIR).as_posix()
     for p in _CAR_DIR.rglob('*') if p.suffix.lower() in _IMAGE_EXTS}
    if _CAR_DIR.is_dir() else set()
)


def car_info(vid: int | None) -> dict:
    """The cars.json entry for a vehicle id ({} when none)."""
    try:
        return CARS.get(int(vid), {})
    except (TypeError, ValueError):
        return {}


def car_specs(vid: int | None) -> dict:
    """Normalized spec fields for the page header / API ({} when no entry)."""
    e = car_info(vid)
    if not e:
        return {}

    def _int(key: str):
        try:
            return int(e.get(key))
        except (TypeError, ValueError):
            return None

    year = _int('year')
    return {
        'year': year or None,
        'power': _int('power'),
        'weight': _int('weight'),
        'engine_cc': _int('engine'),
        'cyl': _int('cyl'),
        'aspiration': str(e.get('aspiration') or ''),
        'transmission': str(e.get('transmission') or ''),
        'engine_placement': str(e.get('enginePlacement') or '').lower(),
        'drivetrain': str(e.get('drivetrainPlacement') or '').lower(),
        'info': str(e.get('info') or ''),
    }


def car_image_url(vid: int | None) -> str:
    """Static URL of the shipped car image; '' when none is on disk."""
    from flask import url_for

    rel = (car_info(vid).get('image') or '').replace('\\', '/')
    if not rel or rel not in _CAR_FILES:
        return ''
    return url_for('static', filename=f'cars/{rel}')


def terminal_stats(vid: int) -> dict:
    """Stage runs for this car: total driven + DNFs. A DNF is a run the game
    closed out unfinished (player_finished=False — same definition as the
    home page's survival rate)."""
    from .models import StageRun

    vid = int(vid)
    total = StageRun.query.filter(StageRun.vehicle_id == vid).count()
    dnf = (StageRun.query
           .filter(StageRun.vehicle_id == vid,
                   StageRun.player_finished.is_(False)).count())
    return {'total': total, 'dnf': dnf}


def location_name(location_id: int | None) -> str:
    """Display name for a LocationId; unknown ids get a generic label."""
    from dr2server.game_data import Location

    try:
        return Location(int(location_id)).display_name
    except (TypeError, ValueError, KeyError):
        return f'Location {location_id}' if location_id is not None else 'Unknown location'


def stage_name(track_model_id: int | None) -> str:
    """Display name for a TrackModelId; unknown ids get a generic label."""
    from dr2server.game_data import Track

    try:
        return Track(int(track_model_id)).display_name
    except (TypeError, ValueError, KeyError):
        return f'Stage {track_model_id}' if track_model_id is not None else 'Unknown stage'


def fastest_stages_by_location(vid: int) -> dict[str, list[dict]]:
    """Per location, how many distinct stages this car holds the best time on —
    overall (fastest of any car) and in-class (fastest among cars sharing this
    car's class). A stage is one route (location + track model); the best comes
    from best_times — the same source as the site leaderboard, where every row
    is a clean finish and rallycross rows are single-lap records (ranking by a
    run's total time would mix 2-lap and 12-lap races). Each row carries the
    stages it wins ('stages' — {name, track_id} entries)."""
    from dr2server.game_data import TRACKS, VEHICLES

    from .db import db
    from .models import TimeTrialEntry

    vid = int(vid)
    target_class = VEHICLES.get(vid, {}).get('class')

    rows = (db.session.query(TimeTrialEntry.track, TimeTrialEntry.vehicle_id,
                             TimeTrialEntry.stage_time_ms)
            .filter(TimeTrialEntry.stage_time_ms > 0)
            .all())
    overall: dict[tuple, tuple] = {}
    in_class: dict[tuple, tuple] = {}
    for track_s, vehicle, ms in rows:
        try:
            tinfo = TRACKS.get(int(track_s))
        except (TypeError, ValueError):
            continue
        if not tinfo:
            continue
        key = (tinfo['location_id'], int(track_s))
        cur = overall.get(key)
        if cur is None or ms < cur[0]:
            overall[key] = (ms, vehicle)
        if target_class is not None \
                and VEHICLES.get(vehicle, {}).get('class') == target_class:
            cur2 = in_class.get(key)
            if cur2 is None or ms < cur2[0]:
                in_class[key] = (ms, vehicle)

    def summarize(best_map: dict[tuple, tuple]) -> list[dict]:
        by_loc: dict[int, list[tuple[str, int]]] = {}
        for (loc, track), (_ms, vehicle) in best_map.items():
            if vehicle == vid:
                by_loc.setdefault(loc, []).append((stage_name(track), track))
        out = []
        for loc, items in by_loc.items():
            items.sort(key=lambda it: it[0].lower())
            out.append({'location': location_name(loc), 'count': len(items),
                        'stages': [{'name': n, 'track_id': t} for n, t in items]})
        out.sort(key=lambda r: (-r['count'], r['location'].lower()))
        return out

    return {'overall': summarize(overall), 'in_class': summarize(in_class)}


def distance_driven_by_user(vid: int) -> list[dict]:
    """Total distance driven in this car, per driver (only entries with
    >0 km), most first."""
    from flask import url_for
    from sqlalchemy import func

    from .countries import flag_url
    from .db import db
    from .models import StageRun, User

    users = {u.username: u for u in User.query.all()}
    rows = (db.session.query(StageRun.username, func.sum(StageRun.distance_m))
            .filter(StageRun.vehicle_id == int(vid),
                    StageRun.distance_m.isnot(None),
                    StageRun.distance_m > 0)
            .group_by(StageRun.username).all())
    out = []
    for username, dist in rows:
        user = users.get(username)
        out.append({
            'username': username,
            'display_name': user.display_name if user else username,
            'flag_url': flag_url((user.country or '') if user else ''),
            'driver_url': url_for('pages.profile', username=username),
            'km': int(dist) / 1000.0,
        })
    out.sort(key=lambda r: (-r['km'], r['username']))
    return out
