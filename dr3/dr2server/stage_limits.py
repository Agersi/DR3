"""Per-stage time limits (the game's max stage time) from a data table.

``stage_time_limits.csv`` next to this file lists every stage/circuit in the
game catalog with its length and the game's per-stage time limit in
milliseconds — the same value the game pre-persists after terminal damage
(its post-wreck leaderboard query carries PlayerBest = the stage limit).
The table is maintained by hand (user-supplied values, 2026-10-28); rally
rows hold the full limit, rallycross rows hold a PER-LAP base that must be
multiplied by the stage's lap count.

Used by the dispatcher's terminal-damage close-out to mark every skipped
stage DNF at its max time when advancing the player to the next service
area (backlog 32, step 3).
"""
import csv
from pathlib import Path
from typing import Dict, Optional

from .game_data import Track

_CSV_PATH = Path(__file__).resolve().parent / "stage_time_limits.csv"

_TABLE: Optional[Dict[int, int]] = None


def _load() -> Dict[int, int]:
    global _TABLE
    if _TABLE is None:
        table: Dict[int, int] = {}
        try:
            with open(_CSV_PATH, encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    try:
                        tid = int((row.get("track_id") or "").strip())
                        val = (row.get("max_time_ms") or "").strip()
                    except (TypeError, ValueError):
                        continue
                    if not val:
                        continue
                    try:
                        table[tid] = int(float(val))
                    except (TypeError, ValueError):
                        continue
        except OSError:
            pass
        _TABLE = table
    return _TABLE


def stage_limit_ms(track_id: int, laps: Optional[int] = None) -> int:
    """The game's time limit for one run of this stage, in milliseconds.

    Rally stages use the stored value as-is.  Rallycross rows store a
    per-lap base, so ``laps`` must be passed to get the full limit for a
    lapped stage.  Returns 0 when the stage is unknown or a rallycross
    limit is requested without a lap count.
    """
    try:
        tid = int(track_id)
    except (TypeError, ValueError):
        return 0
    base = _load().get(tid, 0)
    if not base:
        return 0
    try:
        discipline = Track(tid).discipline
    except ValueError:
        discipline = "rally"
    if discipline == "rallycross":
        try:
            n = int(laps)
        except (TypeError, ValueError):
            return 0
        if n < 1:
            return 0
        return base * n
    return base
