"""Cron sweep for stored events.

Official dailies/weeklies/monthlies are no longer generated here — the game
receives official content from the upstream EgoNet backend. This module only
deactivates expired events so stale rows stop being served.
"""
from __future__ import annotations

from datetime import datetime

from .db import db
from .models import Event, utcnow


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


def run_cron_tick(now: datetime | None = None) -> dict:
    now = now or utcnow()
    expired = deactivate_expired(now)
    return {'created': [], 'expired': expired}
