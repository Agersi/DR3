"""Cron sweep for stored events.

Official dailies/weeklies/monthlies are no longer generated here — the game
receives official content from the upstream EgoNet backend. This module
deactivates expired events so stale rows stop being served, and closes out
finished rounds: every stage a started driver left unfinished when its round
window ended is flagged DNF, so classifications, driver statuses and medals
settle instead of hanging on "in progress" forever.
"""
from __future__ import annotations

import json
from datetime import datetime

from .db import db
from .models import Event, Result, utcnow


def deactivate_expired(now: datetime) -> list[str]:
    expired: list[str] = []
    for e in Event.query.filter_by(active=True).all():
        if e.end_time is None or e.end_time > now:
            continue
        e.active = False
        db.session.add(e)
        expired.append(e.id)
    db.session.commit()
    return expired


def _closed_rounds(event: dict, now: datetime) -> list[tuple[int, int]]:
    """(flat offset, stage count) per round whose window has already ended.

    Rally Seasons close out leg by leg (windows run back-to-back from the
    championship start); Multi-Location rallies and legacy events share one
    whole-event window."""
    from .results_view import leg_windows
    from .scoring import championship_view

    rallies = championship_view(event)
    if not rallies:
        return []
    windows = leg_windows(event)
    closed: list[tuple[int, int]] = []
    for r in rallies:
        end = windows[r['index']][1] if r['index'] < len(windows) else None
        if end is not None and end <= now:
            closed.append((r['offset'], r['count']))
    return closed


def close_out_finished_rounds(now: datetime) -> list[str]:
    """Flag unfinished stages of started drivers as DNF once their round's
    window has ended.

    A driver "started" a round when they took a real time or already carry a
    DNF marker on any stage of it, or were mid-stage when it closed (an
    in_progress entry inside the range) — untouched rounds are non-
    participation and stay blank. Only ``race_status`` is written (4 = DNF);
    stored times are never invented or altered, so bogey-adjusted totals are
    unchanged. Idempotent: stages that already carry a race_status are left
    alone, and nothing inside an open window is ever touched."""
    closed_events: list[str] = []
    for e in Event.query.all():
        closed = _closed_rounds(e.to_dict(), now)
        if not closed:
            continue
        flagged_any = False
        for res in Result.query.filter_by(event_id=e.id).all():
            stages = json.loads(res.stages_json or '[]')
            in_progress = json.loads(res.in_progress_json or '{}')
            changed = False
            for offset, count in closed:
                started: set[int] = set()
                for i in range(offset, offset + count):
                    s = stages[i] if i < len(stages) else None
                    s = s or {}
                    if int(s.get('time_ms', 0) or 0) > 0 \
                            or int(s.get('race_status', 0) or 0) != 0:
                        started.add(i)
                for k in in_progress:
                    try:
                        p = int(k)
                    except ValueError:
                        continue
                    if offset <= p < offset + count:
                        started.add(p)
                if not started:
                    continue
                while len(stages) < offset + count:
                    stages.append({})
                for i in range(offset, offset + count):
                    s = stages[i]
                    # Only genuinely unfinished slots: a real time with no DNF
                    # marker is a clean finish (kept as-is); a non-zero
                    # race_status already is one.
                    if int(s.get('race_status', 0) or 0) != 0:
                        continue
                    if int(s.get('time_ms', 0) or 0) > 0:
                        continue
                    s['race_status'] = 4
                    changed = True
            if changed:
                res.stages_json = json.dumps(stages)
                flagged_any = True
        if flagged_any:
            closed_events.append(e.id)
    if closed_events:
        db.session.commit()
    return closed_events


def run_cron_tick(now: datetime | None = None) -> dict:
    now = now or utcnow()
    expired = deactivate_expired(now)
    closed = close_out_finished_rounds(now)
    return {'created': [], 'expired': expired, 'closed_out': closed}
