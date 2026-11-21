"""Per-car page data (website).

CAR_INFO holds the user-supplied content for each car's page header, keyed by
catalog vehicle id (dr2server.game_data.Vehicle):
  image — file name inside static/cars/
  stats — (label, value) rows shown under the car name
Leaderboard figures are computed from stage_runs at request time.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# User-supplied per-car page content. Add one entry per vehicle id, e.g.:
# CAR_INFO[565] = {
#     'image': 'porsche_911_rgt.png',
#     'stats': [('Power', '380 hp'), ('Weight', '1,400 kg')],
# }
CAR_INFO: dict[int, dict] = {}

_CAR_DIR = Path(__file__).resolve().parent.parent / 'static' / 'cars'
_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
_CAR_FILES = (
    {p.name.lower() for p in _CAR_DIR.glob('*') if p.suffix.lower() in _IMAGE_EXTS}
    if _CAR_DIR.is_dir() else set()
)


def car_info(vid: int | None) -> dict:
    """The user-supplied page content for a vehicle id ({} when none)."""
    try:
        return CAR_INFO.get(int(vid), {})
    except (TypeError, ValueError):
        return {}


def car_image_url(vid: int | None) -> str:
    """Static URL of the shipped car image; '' when none is on disk."""
    from flask import url_for

    img = Path(car_info(vid).get('image') or '').name
    if not img or img.lower() not in _CAR_FILES:
        return ''
    return url_for('static', filename=f'cars/{img}')


def location_name(location_id: int | None) -> str:
    """Display name for a LocationId; unknown ids get a generic label."""
    from dr2server.game_data import Location

    try:
        return Location(int(location_id)).display_name
    except (TypeError, ValueError, KeyError):
        return f'Location {location_id}' if location_id is not None else 'Unknown location'


def fastest_stages_by_location(vid: int) -> dict[str, list[dict]]:
    """Per location, how many distinct stages this car holds the best clean
    time on — overall (fastest of any car) and in-class (fastest among cars
    sharing this car's class). A stage is one route (location + track model);
    the best is the fastest clean finish posted by any driver in any run type."""
    from dr2server.game_data import VEHICLES

    from .db import db
    from .models import StageRun

    vid = int(vid)
    target_class = VEHICLES.get(vid, {}).get('class')

    rows = (db.session.query(StageRun.location_id, StageRun.track_model_id,
                             StageRun.vehicle_id, StageRun.time_ms)
            .filter(StageRun.player_finished.is_(True),
                    StageRun.time_ms > 0,
                    StageRun.location_id.isnot(None),
                    StageRun.track_model_id.isnot(None))
            .all())
    overall: dict[tuple, tuple] = {}
    in_class: dict[tuple, tuple] = {}
    for loc, track, vehicle, ms in rows:
        key = (loc, track)
        cur = overall.get(key)
        if cur is None or ms < cur[0]:
            overall[key] = (ms, vehicle)
        if target_class is not None \
                and VEHICLES.get(vehicle, {}).get('class') == target_class:
            cur2 = in_class.get(key)
            if cur2 is None or ms < cur2[0]:
                in_class[key] = (ms, vehicle)

    def summarize(best_map: dict[tuple, tuple]) -> list[dict]:
        counts: dict[int, int] = {}
        for (loc, _track), (_ms, vehicle) in best_map.items():
            if vehicle == vid:
                counts[loc] = counts.get(loc, 0) + 1
        out = [{'location': location_name(loc), 'count': n} for loc, n in counts.items()]
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
