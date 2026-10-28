"""Bogey-time scoring, per-rally standings and championship points.

Same rules as the original game:
a stage a driver did not finish scores a fixed "bogey time" — 15:00.000 on
sprint stages, 30:00.000 on long stages, 15:00.000 on every rallycross
circuit. "Long" is relative to the stage's own location: each rally location
is authored as 2 long routes plus reverses and 4 sprints plus reverses (the
sprints being halves of the longs), so the 4 longest routes carry the 30
minute bogey. The bogey is a constant, not a cap — slow real finishes above
it stand — it accrues for every unfinished stage, and drivers rank by the
resulting total, interleaved with slow finishers rather than forced to the
bottom.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dr2server.game_data import VEHICLES  # noqa: E402

from .tables import RX_LOCATIONS, STAGES, normalize_championship  # noqa: E402

BOGEY_SHORT_MS = 15 * 60 * 1000
BOGEY_LONG_MS = 30 * 60 * 1000

# Championship points systems (chosen per championship in the builder).
SCORING_WRC = 'wrc'
SCORING_GROUPB = 'groupb'
SCORING_CODEMASTERS = 'codemasters'
SCORING_SYSTEMS: tuple[str, str, str] = (SCORING_WRC, SCORING_GROUPB, SCORING_CODEMASTERS)

# Fixed top-ten classification scales by finishing position.
WRC_POINTS = (25, 17, 15, 12, 10, 8, 6, 4, 2, 1)
GROUPB_POINTS = (20, 15, 12, 10, 8, 6, 4, 3, 2, 1)

# Power-stage bonus: extra points for the top five finishers of each rally's
# final stage (position 1..5).
POWER_STAGE_BONUS = (5, 4, 3, 2, 1)


def codemasters_points(participants: int, position: int) -> int:
    """Codemasters-style points for classification ``position`` (1-based) in a
    field of ``participants`` drivers.

    p_1 = P and p_i = max(p_{i-1} - max(floor(log2(P) + 2 - i), 1), 1), with
    P the number of participants — the leader scores P points and each position
    below drops by a shrinking step, floored at 1."""
    if participants < 1 or position < 1:
        return 0
    p = participants
    for i in range(2, position + 1):
        step = max(math.floor(math.log2(participants) + 2 - i), 1)
        p = max(p - step, 1)
    return p


def fixed_scale_points(system: str, position: int) -> int:
    """Points for a fixed-scale system (WRC/GroupB) at classification
    ``position`` (1-based); 0 outside the top ten."""
    scale = WRC_POINTS if system == SCORING_WRC else GROUPB_POINTS
    return scale[position - 1] if 1 <= position <= len(scale) else 0


def rally_classification_points(system: str, participants: int, position: int) -> int:
    """Points for classification ``position`` (1-based) under the given system,
    where ``participants`` is the size of that rally's field."""
    if system == SCORING_CODEMASTERS:
        return codemasters_points(participants, position)
    return fixed_scale_points(system, position)


def scoring_system_for(event: dict | None) -> str:
    """The championship's configured points system; WRC for events created
    before the option existed (or with an unknown stored value)."""
    if not event:
        return SCORING_WRC
    s = (event.get('settings') or {}).get('scoring')
    return s if s in SCORING_SYSTEMS else SCORING_WRC


def power_stage_enabled(event: dict | None) -> bool:
    """Whether the championship awards the final-stage bonus points."""
    if not event:
        return False
    return bool((event.get('settings') or {}).get('power_stage', False))


def duration_label(d: dict) -> str:
    """Human label for a {days, hours, mins} duration, e.g. "2 Days 6 Hours"."""
    d = d or {}
    parts = []
    if d.get('days'):
        parts.append(f"{d['days']} Day" + ('s' if d['days'] != 1 else ''))
    if d.get('hours'):
        parts.append(f"{d['hours']} Hour" + ('s' if d['hours'] != 1 else ''))
    if d.get('mins'):
        parts.append(f"{d['mins']} Min" + ('s' if d['mins'] != 1 else ''))
    return ' '.join(parts) if parts else '0 Mins'


def championship_view(event: dict) -> list[dict]:
    """Per-rally view of a championship for the detail pages: one entry per
    sub-event with its stages and the flat stage-ordinal offset/count so the
    per-rally leaderboard can slice the flat results array."""
    champ = normalize_championship(event)
    out: list[dict] = []
    offset = 0
    for ei, ev in enumerate(champ['events']):
        stages = ev.get('stages', []) or []
        out.append({
            'index': ei,
            'location': ev.get('location', ''),
            'car_class': ev.get('car_class', ''),
            'surface': ev.get('surface', ''),
            'duration_label': duration_label(ev.get('duration', {})),
            'stages': stages,
            'offset': offset,
            'count': len(stages),
        })
        offset += len(stages)
    return out


def long_stage_cutoff_km(location: str) -> float | None:
    """Distance at or above which a stage of this location scores the long
    bogey: the 4th-longest route of the location's full roster. ``None``
    (everything short) for rallycross circuits, unknown locations, and
    rosters too small to have a long/sprint split."""
    if location in RX_LOCATIONS:
        return None
    kms = sorted((km for _name, km in STAGES.get(location, [])), reverse=True)
    if len(kms) < 5:
        return None
    return kms[3]


def flat_stage_bogeys_ms(event: dict) -> list[int]:
    """Per flat stage ordinal, the bogey time an unfinished stage scores."""
    out: list[int] = []
    for rally in championship_view(event):
        cutoff = long_stage_cutoff_km(rally['location'])
        for s in rally['stages']:
            km = float((s or {}).get('distance_km') or 0)
            out.append(BOGEY_LONG_MS if cutoff is not None and km >= cutoff
                       else BOGEY_SHORT_MS)
    return out


def adjusted_total_ms(entry: dict, bogeys: list[int], offset: int = 0) -> int:
    """Total the way the game scored it: real time + penalties where a stage
    was finished, that stage's bogey time where it wasn't. ``bogeys`` are the
    per-stage bogey times starting at flat ordinal ``offset``."""
    stages = entry.get('stages') or []
    total = 0
    for i, bogey in enumerate(bogeys):
        s = stages[offset + i] if offset + i < len(stages) else None
        t = int((s or {}).get('time_ms', 0) or 0)
        if t > 0:
            total += t + int((s or {}).get('penalties_ms', 0) or 0)
        else:
            total += bogey
    return total


def stages_completed(entry: dict) -> int:
    """Stages this entry has a real time for. Padding zeros (added when a
    driver first submits mid-event) don't count."""
    return sum(1 for s in (entry.get('stages') or [])
               if s and int(s.get('time_ms', 0) or 0) > 0)


def overall_rank_key(event: dict):
    """Sort key for a championship's overall standings: bogey-adjusted total,
    with more completed stages breaking exact ties in favor of real times."""
    bogeys = flat_stage_bogeys_ms(event)
    return lambda e: (adjusted_total_ms(e, bogeys), -stages_completed(e))


def car_label(entry: dict) -> str:
    """Human-readable car for a results entry.

    Entries created by the game only carry a numeric ``vehicle_id`` (and the
    first write for a driver is the leaderboard pre-persist, which carries no
    car at all, so ``car`` is usually blank). Resolve the name from the
    vehicle id; fall back to whatever ``car`` text is stored.
    """
    vid = entry.get('vehicle_id')
    car = str(entry.get('car') or '')
    if vid is None and car.isdigit():
        vid = car
    try:
        vid_int = int(vid) if vid is not None else None
    except (TypeError, ValueError):
        vid_int = None
    if vid_int is not None:
        meta = VEHICLES.get(vid_int)
        if meta:
            return str(meta['name'])
        if not car:
            return f'Vehicle {vid_int}'
    return car


def rally_standings(entries: list[dict], rally: dict) -> list[dict]:
    """Per-rally leaderboard: every championship entry, scored over this
    rally's stage range with bogey times for unfinished stages and sorted by
    that subtotal. Rows carry ``dnf_stages`` (unfinished stages in this
    rally) so the UI can flag them and points can skip them."""
    offset, count = rally['offset'], rally['count']
    cutoff = long_stage_cutoff_km(rally['location'])
    bogeys = [BOGEY_LONG_MS if cutoff is not None
              and float((s or {}).get('distance_km') or 0) >= cutoff
              else BOGEY_SHORT_MS for s in rally['stages']]
    rows: list[dict] = []
    for e in entries:
        seg = (e.get('stages', []) or [])[offset:offset + count]
        seg += [None] * (count - len(seg))
        # A stage with a non-zero race_status is a DNF close-out (it may still
        # carry the pre-persisted time-limit value) — it doesn't count as done.
        done = sum(1 for s in seg
                   if s and int(s.get('time_ms', 0) or 0) > 0
                   and int(s.get('race_status', 0) or 0) == 0)
        rows.append({
            'username': e.get('username', ''),
            'car': car_label(e),
            'vehicle_id': e.get('vehicle_id'),
            'stages': seg,
            'total_time_ms': adjusted_total_ms({'stages': seg}, bogeys),
            'dnf_stages': count - done,
        })
    rows.sort(key=lambda r: (r['total_time_ms'], r['dnf_stages']))
    return rows


def overall_standings(event: dict, entries: list[dict]) -> list[dict]:
    """Championship-wide standings for the site: every driver, ranked by
    bogey-adjusted total. Rows carry ``stages_done``/``stages_total`` so
    templates can flag partial entries; ``total_time_ms`` is the adjusted
    total, as the game displayed it."""
    bogeys = flat_stage_bogeys_ms(event)
    stages_total = len(bogeys)
    rows = [{
        'username': e.get('username', ''),
        'car': car_label(e),
        'vehicle_id': e.get('vehicle_id'),
        'total_time_ms': adjusted_total_ms(e, bogeys),
        'stages_done': stages_completed(e),
        'stages_total': stages_total,
    } for e in entries]
    rows.sort(key=lambda r: (r['total_time_ms'], -r['stages_done']))
    return rows


def _ran_any_stage(row: dict) -> bool:
    """Whether a rally-standings row has a real time on at least one stage of
    the rally (i.e. the driver actually took part in it)."""
    return any(s and int(s.get('time_ms', 0) or 0) > 0 for s in row['stages'])


def championship_points(event: dict, entries: list[dict]) -> dict[str, int]:
    """Championship points per username.

    Each rally of the championship is classified over every driver who took a
    real stage time in it (``rally_standings``, bogey-adjusted, fastest first).
    A driver's classification position earns points from the championship's
    scoring system — WRC/GroupB fixed top-ten scales or the field-size
    Codemasters formula — but only if they finished every stage of that rally.
    A DNF takes up its classification position and scores nothing under
    WRC/GroupB; under Codemasters, taking part still earns the minimum 1 point.
    When power-stage points are enabled, the top five finishers of each rally's
    FINAL stage earn an extra bonus (5/4/3/2/1) on top of the classification
    points. A driver's total is the sum across rallies; every driver in
    ``entries`` is present in the result, with 0 if they scored nothing.
    """
    points: dict[str, int] = {e.get('username', ''): 0 for e in entries}
    system = scoring_system_for(event)
    power_stage = power_stage_enabled(event)
    for rally in championship_view(event):
        rows = rally_standings(entries, rally)
        participants = [r for r in rows if _ran_any_stage(r)]
        p_total = len(participants)
        for pos, row in enumerate(participants, start=1):
            name = row['username']
            if row['dnf_stages'] != 0:
                # DNF: takes its position but no classification points — except
                # Codemasters, where taking part earns the minimum 1 point.
                pts = 1 if system == SCORING_CODEMASTERS else 0
            else:
                pts = rally_classification_points(system, p_total, pos)
            points[name] = points.get(name, 0) + pts
        if power_stage and rally['count'] > 0:
            final_idx = rally['count'] - 1
            runners: list[tuple[int, str]] = []
            for row in rows:
                s = row['stages'][final_idx] if final_idx < len(row['stages']) else None
                t = int((s or {}).get('time_ms', 0) or 0)
                if t > 0:
                    runners.append((t + int((s or {}).get('penalties_ms', 0) or 0),
                                    row['username']))
            runners.sort(key=lambda x: x[0])
            for i, (_t, name) in enumerate(runners[:len(POWER_STAGE_BONUS)]):
                points[name] = points.get(name, 0) + POWER_STAGE_BONUS[i]
    return points
