"""Per-stage run detail: pair the StageEnded telemetry snapshot with the
StageComplete RPC and record the combined row.

The game reports every finished stage twice, on two independent channels whose
arrival order is not guaranteed:

- ``RaceNetChallenges.StageComplete`` (RPC) — car, time, distance, wheel/assists
  flags. Club stages are served locally by the dispatcher; official dailies and
  time trials are proxied to EGO and read on the way through.
- ``DataMining.DataEvent`` with event name ``StageEnded`` — a 79-field snapshot
  (always proxied upstream in default mode) carrying input device, camera
  metres, tyre/surface state, weather preset, VR flag and the driver's global
  play counters.

This buffer keys both halves on ``(challenge_id, event_index, stage_index)`` —
the same ids both payloads carry — merges them as soon as both are present, and
hands the combined record to a callback (which posts it to the web API in a
daemon thread). A timeout flush covers runs where only one half ever arrives
(e.g. DNF close-outs), so nothing is silently dropped.

Only extracted fields are kept — the raw payload never leaves the client except
as the small merged dict the web side stores.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional, Tuple

# StageEnded snapshot fields worth keeping (indices in the 79-field payload).
STAGE_ENDED_FIELDS: Tuple[str, ...] = (
    # correlation + routing context
    "challenge_id", "event_index", "stage_index", "challenge_type",
    "track_id", "track_model_id", "location_id",
    "vehicle_class_id", "vehicle_id",
    # outcome
    "player_finished", "player_end_status", "race_position",
    "stage_time_seconds",
    # conditions / setup (stage_conditions_id is the FULL condition id — same
    # space as TimeTrial.PostTime's ConditionsId, so it keys personal bests)
    "weather_preset_id", "stage_conditions_id",
    "surface_degradation_enabled", "surface_degradation_level",
    "is_hardcore_damage_enabled", "is_any_assists_enabled",
    "tyre_compound_id", "tyre_wear_state",
    "vr_enabled",
    # camera metres (per-camera distance driven this stage)
    "metres_cam_bonnet", "metres_cam_bumper", "metres_cam_chase_close",
    "metres_cam_chase_far", "metres_cam_dash", "metres_cam_head",
    # input device
    "library_id", "device_hardware_id", "driver_name",
    # driver's global play counters (lifetime, from the game itself)
    "g_separate_days_played_count", "g_total_seconds_played",
    "g_session_count",
)

# Fields the RPC half contributes. The dispatcher/proxy paths extract these
# from their respective payload shapes and hand them to merge_rpc().
RPC_FIELDS: Tuple[str, ...] = (
    "challenge_id", "event_index", "stage_index",
    "vehicle_id", "livery_id",
    "stage_time_ms", "race_status", "recov_to_service",
    "meters_driven", "distance_driven",
    "using_wheel", "using_assists",
    # club path only: the web event id (official/TT runs carry none)
    "event_id",
    # club clean finishes only: telemetry splits (seconds) + the flat stage
    # ordinal within the championship (correlates with results.stages_json)
    "splits", "flat_stage_index",
)

_KEY = Tuple[Optional[int], Optional[int], Optional[int]]


def _opt_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


class StageDetailBuffer:
    """Pairs StageEnded + StageComplete per stage and emits merged records.

    ``send`` receives the merged dict; it runs on a daemon thread so a slow or
    failing web call can never touch the game-facing request path.
    """

    FLUSH_TIMEOUT_S = 15.0

    def __init__(self, send: Callable[[Dict[str, Any]], None]) -> None:
        self._send = send
        self._lock = threading.Lock()
        self._pending: Dict[_KEY, Dict[str, Dict[str, Any]]] = {}
        self._timers: Dict[_KEY, threading.Timer] = {}

    # ------------------------------------------------------------------
    # Feeding (called from the request path — must stay fast + thread-safe)
    # ------------------------------------------------------------------

    def feed_datamining(self, fields: Dict[str, Any]) -> None:
        """StageEnded snapshot arrived (event name already checked by caller)."""
        key = (
            _opt_int(fields.get("challenge_id")),
            _opt_int(fields.get("event_index")),
            _opt_int(fields.get("stage_index")),
        )
        half = {k: fields[k] for k in STAGE_ENDED_FIELDS if k in fields}
        self._merge(key, "datamining", half)

    def merge_rpc(self, key: _KEY, rpc_fields: Dict[str, Any]) -> None:
        """StageComplete RPC payload arrived (club or proxied official/TT)."""
        half = {k: rpc_fields[k] for k in RPC_FIELDS if k in rpc_fields}
        self._merge(key, "rpc", half)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _merge(self, key: _KEY, name: str, half: Dict[str, Any]) -> None:
        with self._lock:
            rec = self._pending.get(key)
            if rec is not None and name in rec:
                # Same half twice (replayed payload) — keep the newer one.
                rec[name].update(half)
            else:
                rec = self._pending.setdefault(key, {})
                rec[name] = half
            self._arm_timer_locked(key)
            merged = self._try_merge_locked(key)
        if merged is not None:
            self._emit(merged)

    def _arm_timer_locked(self, key: _KEY) -> None:
        timer = self._timers.get(key)
        if timer is not None:
            timer.cancel()
        timer = threading.Timer(self.FLUSH_TIMEOUT_S, self._timeout_flush, [key])
        timer.daemon = True
        self._timers[key] = timer
        timer.start()

    def _try_merge_locked(self, key: _KEY) -> Optional[Dict[str, Any]]:
        rec = self._pending.get(key)
        if not rec or "datamining" not in rec or "rpc" not in rec:
            return None
        del self._pending[key]
        timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        merged: Dict[str, Any] = {}
        merged.update(rec["datamining"])
        # RPC ints are exact — let them win over the snapshot's floats where
        # both carry a value (vehicle id, time).
        merged.update({k: v for k, v in rec["rpc"].items() if v is not None})
        return merged

    def _timeout_flush(self, key: _KEY) -> None:
        with self._lock:
            rec = self._pending.pop(key, None)
            self._timers.pop(key, None)
            if rec is not None:
                merged: Dict[str, Any] = {}
                for half in rec.values():
                    merged.update({k: v for k, v in half.items() if v is not None})
        if merged:
            self._emit(merged)

    def _emit(self, record: Dict[str, Any]) -> None:
        try:
            threading.Thread(target=self._send, args=(record,),
                             name="dr3-stage-detail", daemon=True).start()
        except RuntimeError:
            # Interpreter shutting down — nothing left to talk to anyway.
            pass
