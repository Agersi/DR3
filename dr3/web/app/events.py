"""Event and result storage helpers (ORM-backed)."""
from __future__ import annotations

import json
import re
from datetime import datetime

from .db import db
from .models import Event, Result, utcnow
from .tables import normalize_championship

_SAFE_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def validate_id(value: str) -> str:
    if not value or not _SAFE_ID_RE.match(value):
        raise ValueError('invalid id')
    return value


def get_event(event_id: str) -> dict | None:
    e = db.session.get(Event, event_id)
    return e.to_dict() if e else None


def get_all_events() -> list[dict]:
    return [e.to_dict() for e in Event.query.all()]


def event_is_active(e: dict, now: datetime | None = None) -> bool:
    """Active flag plus the real time window: a scheduled championship is not
    live until its start passes, and an expired event never serves even if
    the cron sweep that flips ``active`` has not run yet."""
    if not e.get('active'):
        return False
    now = now or utcnow()
    start = e.get('start_time')
    if start:
        try:
            if datetime.fromisoformat(start) > now:
                return False
        except ValueError:
            pass
    end = e.get('end_time')
    if not end:
        return True
    try:
        return datetime.fromisoformat(end) > now
    except ValueError:
        return True


def event_is_upcoming(e: dict, now: datetime | None = None) -> bool:
    """Scheduled but not started yet (never served to the game)."""
    if not e.get('active'):
        return False
    start = e.get('start_time')
    if not start:
        return False
    try:
        return datetime.fromisoformat(start) > (now or utcnow())
    except ValueError:
        return False


def event_sort_key(e: dict, now: datetime | None = None) -> tuple[int, float, str]:
    """Listing order: live first, then upcoming, then ended. Within a bucket,
    live events run soonest-ending first, upcoming soonest-starting first,
    ended most-recently-finished first. Event id is the final tiebreak."""
    now = now or utcnow()

    def _ts(raw) -> float | None:
        try:
            return datetime.fromisoformat(raw).timestamp()
        except (TypeError, ValueError):
            return None

    from .results_view import effective_end

    eid = str(e.get('id', ''))
    if event_is_upcoming(e, now):
        start = _ts(e.get('start_time'))
        return (1, start if start is not None else float('inf'), eid)
    if event_is_active(e, now):
        # Live seasons order by their CURRENT leg's window end, not the last
        # leg's (see effective_end); multi/legacy events are unchanged.
        end = effective_end(e, now)
        return (0, end.timestamp() if end is not None else float('inf'), eid)
    end = _ts(e.get('end_time'))
    return (2, -end if end is not None else float('inf'), eid)


def sort_events(events: list[dict], now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    return sorted(events, key=lambda e: event_sort_key(e, now))


def champ_stage_layout(event: dict) -> list[int]:
    """Stage counts per sub-event; ``[len(stages)]`` for legacy single events."""
    evs = event.get('events')
    if evs:
        return [len(ev.get('stages', []) or []) for ev in evs]
    return [len(event.get('stages', []) or [])]


def global_stage_index(event: dict, event_index: int, stage_index: int) -> int:
    """Map a per-event ``(event_index, stage_index)`` to a flat championship-wide
    stage ordinal so results for different sub-events don't collide. For
    ``event_index == 0`` this is exactly ``stage_index``."""
    if event_index <= 0:
        return stage_index
    layout = champ_stage_layout(event)
    return sum(layout[:event_index]) + stage_index


def get_result(event_id: str, username: str) -> Result | None:
    return Result.query.filter_by(event_id=event_id, username=username).first()


def upsert_result(event_id: str, username: str) -> Result:
    r = get_result(event_id, username)
    if r is None:
        r = Result(event_id=event_id, username=username)
        db.session.add(r)
        db.session.flush()
    return r


def entry_to_dict(r: Result) -> dict:
    return {
        'username': r.username,
        'car': r.car or '',
        'vehicle_id': r.vehicle_id,
        'total_time_ms': r.total_time_ms,
        'attempts_used': r.attempts_used,
        'stages': json.loads(r.stages_json or '[]'),
    }


def entry_nationality(entry: dict) -> int:
    """The player's most recently reported nationality id (game ids are
    passed through as-is; the game renders its own flag from them). 0 when
    never reported."""
    for s in reversed(entry.get('stages') or []):
        nat = int((s or {}).get('nationality_id', 0) or 0)
        if nat:
            return nat
    return 0


def entries_for_event(event_id: str) -> list[dict]:
    rows = Result.query.filter_by(event_id=event_id).order_by(Result.id).all()
    return [entry_to_dict(r) for r in rows]
