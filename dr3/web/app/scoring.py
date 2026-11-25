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

from dr2server.game_data import VEHICLE_CLASSES, VEHICLES  # noqa: E402

from .tables import CHAMP_MODE_SEASON, RX_LOCATIONS, STAGES, normalize_championship  # noqa: E402

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


def fresh_damage_indices(event: dict | None) -> set[int]:
    """Flat stage ordinals where a driver's damage starts from zero.

    Always the event's first stage. Rally Seasons additionally reset at the
    first stage of every round — each season round is its own in-game club
    event, so damage does not carry over between rounds (Multi-Location
    rallies accumulate damage continuously across legs)."""
    out = {0}
    if ((event or {}).get('settings') or {}).get('mode') == CHAMP_MODE_SEASON:
        for rally in championship_view(event):
            out.add(rally['offset'])
    return out


def tyre_set_changed(prev_entry: dict | None, cur_entry: dict | None) -> bool:
    """True when a new physical tyre set was fitted before ``cur_entry``.

    A different compound is always a different set; the same compound with
    one fewer set left (``tyres_remaining`` decremented) means a spare of it
    was fitted at service. Missing fields on either side simply disable the
    corresponding check."""
    def _val(entry: dict | None, key: str) -> int | None:
        if not isinstance(entry, dict):
            return None
        try:
            v = entry.get(key)
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    pc, cc = _val(prev_entry, 'tyre_compound'), _val(cur_entry, 'tyre_compound')
    if pc is not None and cc is not None and pc != cc:
        return True
    pr, cr = _val(prev_entry, 'tyres_remaining'), _val(cur_entry, 'tyres_remaining')
    return pr is not None and cr is not None and cr < pr


def fresh_tyre_start(d0: dict | None) -> dict | None:
    """A copy of a comp_damage snapshot with the per-tyre state zeroed — the
    start of a freshly fitted set (wear + impact). The rest of the car keeps
    its carried-over damage. Non-dicts pass through unchanged."""
    if not isinstance(d0, dict):
        return d0
    d = dict(d0)
    d['wheels_wear'] = 0
    d['wheels_impact'] = 0
    return d


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


def vehicle_meta(vid):
    """(display name, class label) for a vehicle id. Unknown ids get a
    generic name and no class so links to them still render a page."""
    try:
        vid_int = int(vid)
    except (TypeError, ValueError):
        return None, ''
    meta = VEHICLES.get(vid_int)
    if not meta:
        return f'Vehicle {vid_int}', ''
    return str(meta['name']), VEHICLE_CLASSES.get(int(meta['class']), '')


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


def championship_points_rounds(event: dict) -> list[dict]:
    """Points rounds for a championship, grouped by mode.

    Rally Seasons classify each leg separately (one round per leg). Multi-Location
    rallies and legacy events run as one championship, so points are awarded only
    once over the whole event — a single round spanning every leg, the same
    grouping as the combined board and the whole-event member medals. Each entry
    carries the flat stage range to classify (``offset``/``count``) plus display
    fields."""
    rallies = championship_view(event)
    if not rallies:
        return []
    if normalize_championship(event)['mode'] == CHAMP_MODE_SEASON:
        return [{
            'index': r['index'],
            'offset': r['offset'],
            'count': r['count'],
            'label': f'R{r["index"] + 1}',
            'location': r['location'],
        } for r in rallies]
    locations = [r['location'] for r in rallies if r['location']]
    return [{
        'index': 0,
        'offset': 0,
        'count': sum(r['count'] for r in rallies),
        'label': 'R1',
        'location': ', '.join(dict.fromkeys(locations)),
    }]


def _range_standings(entries: list[dict], offset: int, count: int,
                     bogeys: list[int]) -> list[dict]:
    """Standings over the flat stage range ``[offset, offset+count)``.

    Same row shape and ordering as ``rally_standings``, but per-stage bogeys come
    from ``bogeys`` (see ``flat_stage_bogeys_ms``), so a round may span several
    locations — each stage is judged against its own location's bogey."""
    rows: list[dict] = []
    for e in entries:
        seg = (e.get('stages', []) or [])[offset:offset + count]
        seg += [None] * (count - len(seg))
        done = sum(1 for s in seg
                   if s and int(s.get('time_ms', 0) or 0) > 0
                   and int(s.get('race_status', 0) or 0) == 0)
        rows.append({
            'username': e.get('username', ''),
            'car': car_label(e),
            'vehicle_id': e.get('vehicle_id'),
            'stages': seg,
            'total_time_ms': adjusted_total_ms({'stages': seg}, bogeys[offset:offset + count]),
            'dnf_stages': count - done,
        })
    rows.sort(key=lambda r: (r['total_time_ms'], r['dnf_stages']))
    return rows


def championship_points_breakdown(event: dict, entries: list[dict]) -> dict[str, list[dict]]:
    """Per-round points breakdown for every driver in the championship.

    ``{username: [one cell per points round]}`` where each cell is ``{'points':
    int, 'bonus': int}`` — the classification points from the scoring system and
    the power-stage bonus (0 when disabled or unearned), aligned with
    ``championship_points_rounds``. Points rounds follow the championship mode:
    Rally Seasons classify each leg separately; Multi-Location rallies and legacy
    events are classified once over the whole event (all legs combined). Same
    rules as ``championship_points``; a driver's total is the sum of their cells."""
    rounds = championship_points_rounds(event)
    out: dict[str, list[dict]] = {
        e.get('username', ''): [{'points': 0, 'bonus': 0} for _ in rounds]
        for e in entries}
    system = scoring_system_for(event)
    power_stage = power_stage_enabled(event)
    bogeys = flat_stage_bogeys_ms(event)
    for rnd in rounds:
        offset, count = rnd['offset'], rnd['count']
        rows = _range_standings(entries, offset, count, bogeys)
        participants = [r for r in rows if _ran_any_stage(r)]
        p_total = len(participants)
        for pos, row in enumerate(participants, start=1):
            cells = out.get(row['username'])
            if cells is None:
                continue
            cell = cells[rnd['index']]
            # Position points for every participant — a DNF takes up its
            # classification position and scores like any other finisher
            # (positions outside a fixed scale's top ten score nothing).
            cell['points'] = rally_classification_points(system, p_total, pos)
        if power_stage and count > 0:
            final_idx = count - 1
            runners: list[tuple[int, str]] = []
            for row in rows:
                s = row['stages'][final_idx] if final_idx < len(row['stages']) else None
                t = int((s or {}).get('time_ms', 0) or 0)
                if t > 0:
                    runners.append((t + int((s or {}).get('penalties_ms', 0) or 0),
                                    row['username']))
            runners.sort(key=lambda x: x[0])
            for i, (_t, name) in enumerate(runners[:len(POWER_STAGE_BONUS)]):
                cells = out.get(name)
                if cells is not None:
                    cells[rnd['index']]['bonus'] += POWER_STAGE_BONUS[i]
    return out


def championship_points(event: dict, entries: list[dict]) -> dict[str, int]:
    """Championship points per username.

    Each points round of the championship is classified over every driver who
    took a real stage time in it (bogey-adjusted, fastest first). Points rounds
    follow the championship mode: Rally Seasons classify each leg separately;
    Multi-Location rallies and legacy events are classified once over the whole
    event. A driver's classification position earns points from the
    championship's scoring system — WRC/GroupB fixed top-ten scales or the
    field-size Codemasters formula — regardless of whether they finished every
    stage of that round: a DNF takes up its classification position and scores
    like any other finisher (positions outside a fixed scale's top ten score
    nothing). When power-stage points are enabled, the top five finishers of each
    round's FINAL stage earn an extra bonus (5/4/3/2/1) on top of the
    classification points. A driver's total is the sum across rounds; every
    driver in ``entries`` is present in the result, with 0 if they scored
    nothing."""
    breakdown = championship_points_breakdown(event, entries)
    return {name: sum(c['points'] + c['bonus'] for c in cells)
            for name, cells in breakdown.items()}
