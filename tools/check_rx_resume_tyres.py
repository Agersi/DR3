"""Regression check: target-stage-aware tyre serving on Progress entries.

The game's internal RX tyre representation (compound >= 13) is valid on RX
stages and invalid on rally stages, and rally values are the other way round
— restoring the wrong discipline's values crashes the client when loading the
target stage.  The progress builders must therefore serve:

  * an RX target    -> the persisted values verbatim (they ARE the game's own
                        internal representation for that stage);
  * a rally target  -> upstream defaults 7/2 when the persisted state carries
                        RX values (the original bug #17 carry-over case).

A State=1 RESUME of an RX stage still crashes a rally car even with as-is
tyre values (verified 2026-11-27 on v0.1.88), so every in-progress entry
whose target stage is RX and whose vehicle is not an RX car is served with
State=0 (between stages) instead — the game fires a fresh StageBegin for
that stage.  Rally targets keep State=1; finished entries keep State=2.

Every Progress entry must ALSO carry VehicleMud (the persisted snapshot from
the last state-carrying stage): an entry without it zeroed the game's mud
state and crashed an RX stage rejoin — byte-for-byte the only difference vs
the proven-working StageComplete shape (diffed 2026-11-27, fixed v0.1.90).
Legacy rows without a stored snapshot serve a zero-filled struct.

Run:  python tools/check_rx_resume_tyres.py
Exits non-zero on the first failed expectation.
"""
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.dispatcher import RpcDispatcher as Dispatcher  # noqa: E402

# Location names as stored on web events -> real game_data Location ids.
RX_LOC = "Bikernieki"    # 41 (rallycross) — the 2026-11-27 crash location
RALLY_LOC = "Sweden"     # 14 (rally)

# The exact persisted state from the 2026-11-27 capture: rally car 534 on an
# RX stage, game-reported internal tyre values.
TUNING_B64 = base64.b64encode(b"\x01" * 140).decode()
MUD_STATE = {
    "dirt": 0.48963165,
    "wheel_mud_0": 0.0,
    "wheel_mud_1": 0.0,
    "wheel_mud_2": 0.0,
    "wheel_mud_3": 0.0,
    "mud": 0.48963165,
    "clean_height": 0.0,
    "clean_dirt": 0.0,
    "clean_mud": 0.0,
}
EXPECTED_MUD = {
    "Dirt": 0.48963165,
    "WheelMud0": 0.0,
    "WheelMud1": 0.0,
    "WheelMud2": 0.0,
    "WheelMud3": 0.0,
    "Mud": 0.48963165,
    "CleanHeight": 0.0,
    "CleanDirt": 0.0,
    "CleanMud": 0.0,
}
STAGE_STATE = {
    "stage_index": 0,
    "time_ms": 120611,
    "meters_driven": 2683,
    "vehicle_id": 534,
    "livery_id": 2899,
    "tyre_compound": 13,
    "tyres_remaining": 0,
    "tuning_setup_b64": TUNING_B64,
    "vehicle_damage": {},
    "has_repaired": False,
    "repair_penalty_ms": 0,
    "vehicle_mud": dict(MUD_STATE),
}


class StubApi:
    def resolve_location_id(self, name):
        return {RX_LOC: 41, RALLY_LOC: 14}.get(name)

    def get_my_progress(self):
        raise AssertionError("must be patched in tests")


def make_dispatcher(my_progress):
    disp = Dispatcher(account_store=None, api_client=StubApi())
    disp._get_my_progress_safe = lambda: my_progress
    return disp


def rx_leg(stages, laps=None):
    leg = {
        "location": RX_LOC,
        "car_class": "Super 1600",
        "stages": [{"track_id": i, "conditions_id": 1} for i in range(stages)],
    }
    if laps is not None:
        leg["rx_laps"] = laps
    return leg


def rally_leg(stages):
    return {
        "location": RALLY_LOC,
        "car_class": "Group N",
        "stages": [{"track_id": i, "conditions_id": 1} for i in range(stages)],
    }


CHECKS = []


def check(label, actual, expected):
    ok = actual == expected
    CHECKS.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {actual!r}"
          + ("" if ok else f", expected {expected!r}"))


def _v(x):
    # egonet.UInt32 wrappers carry the wire value in .value
    return x.value if hasattr(x, "value") else x


def tyre(entry):
    return _v(entry["TyreCompound"]), _v(entry["TyresRemaining"])


def mud(entry):
    return entry.get("VehicleMud")


# ── A. _build_user_progress: single-leg RX championship, rally car ─────────
# The 2026-11-27 crash shape: stage 0 done (compound 13 persisted), rejoin at
# stage 1.  v0.1.87 served 7/2 State=1 -> crash; v0.1.88 served 13/2 State=1
# -> still crash; now State=0 so the game starts stage 1 fresh.
print("A. single-leg RX resume (the 2026-11-27 crash shape)")
disp = make_dispatcher({"events": [{
    "event_id": "evt-test",
    "completed_stages": [dict(STAGE_STATE)],
    "total_time_ms": 120611,
    "attempts_used": 0,
}]})
evt = {
    "id": "evt-test",
    "location": RX_LOC,
    "car_class": "Super 1600",
    "stages": [{"track_id": i} for i in range(3)],
    "events": [rx_leg(3, laps=2)],
}
disp._challenge_event_map[999] = "evt-test"
entries = disp._build_user_progress([evt])
check("entry count", len(entries), 1)
e = entries[0]
check("StageIndex", e["StageIndex"], 1)
check("State (fresh StageBegin, not resume)", e["State"], 0)
check("(TyreCompound, TyresRemaining)", tyre(e), (13, 2))
check("AttemptsLeft", e["AttemptsLeft"], 1)
# v0.1.90: the entry must carry VehicleMud — its absence zeroed the game's
# mud state and crashed this exact rejoin (the only field missing vs the
# proven-working StageComplete shape, diffed 2026-11-27).
check("VehicleMud present + round-trips", mud(e), EXPECTED_MUD)

# ── A2. same event, fully completed -> State=2, last stage, 0 attempts ─────
print("A2. single-leg RX, all stages done")
disp = make_dispatcher({"events": [{
    "event_id": "evt-test",
    "completed_stages": [dict(STAGE_STATE, stage_index=i) for i in range(3)],
    "total_time_ms": 120611 * 3,
    "attempts_used": 0,
}]})
disp._challenge_event_map[999] = "evt-test"
e = disp._build_user_progress([evt])[0]
check("StageIndex", e["StageIndex"], 2)
check("State", e["State"], 2)
check("AttemptsLeft", e["AttemptsLeft"], 0)
check("VehicleMud present + round-trips", mud(e), EXPECTED_MUD)

# ── B. _multi_event_progress: mid-leg resume ON an RX leg (as-is) ──────────
print("B. multi-leg, mid-leg resume on the active RX leg")
disp = make_dispatcher({"events": []})
ep = {"completed_stages": [dict(STAGE_STATE)], "attempts_used": 0}
e = disp._multi_event_progress(
    111, layout=[3, 4], active=0, ep=ep,
    carry_over=True, prev_was_rx=False, target_is_rx=True)
check("StageIndex", e["StageIndex"], 1)
check("State (fresh StageBegin, not resume)", e["State"], 0)
check("(TyreCompound, TyresRemaining)", tyre(e), (13, 2))
check("VehicleMud present + round-trips", mud(e), EXPECTED_MUD)

# ── C. carry-over RX leg -> rally leg (original #17 case: seed 7/2) ────────
print("C. multi-leg, carry-over into a rally leg after an RX leg")
ep = {"completed_stages": [dict(STAGE_STATE, stage_index=i) for i in range(3)],
      "attempts_used": 0}
e = disp._multi_event_progress(
    112, layout=[3, 4], active=1, ep=ep,
    carry_over=True, prev_was_rx=True, target_is_rx=False)
check("StageIndex", e["StageIndex"], 0)
check("State", e["State"], 1)
check("(TyreCompound, TyresRemaining)", tyre(e), (7, 2))
check("VehicleMud present + round-trips", mud(e), EXPECTED_MUD)

# ── D. carry-over RX leg -> back-to-back RX leg (as-is tyres, State=0) ─────
print("D. multi-leg, carry-over into a second RX leg")
e = disp._multi_event_progress(
    113, layout=[3, 4], active=1, ep=ep,
    carry_over=True, prev_was_rx=True, target_is_rx=True)
check("State (fresh start of the leg, not resume)", e["State"], 0)
check("(TyreCompound, TyresRemaining)", tyre(e), (13, 2))
check("VehicleMud present + round-trips", mud(e), EXPECTED_MUD)

# ── E. mid-leg resume on a rally leg with RX values persisted (defensive) ──
print("E. multi-leg, mid-leg resume on a rally leg (RX values must sanitize)")
ep = {"completed_stages": [dict(STAGE_STATE, stage_index=i) for i in range(4)],
      "attempts_used": 0}
e = disp._multi_event_progress(
    114, layout=[3, 4], active=1, ep=ep,
    carry_over=True, prev_was_rx=False, target_is_rx=False)
check("StageIndex", e["StageIndex"], 1)
check("State (rally resume untouched)", e["State"], 1)
check("(TyreCompound, TyresRemaining)", tyre(e), (7, 2))
check("VehicleMud present + round-trips", mud(e), EXPECTED_MUD)

# ── F. rally -> rally: plain values pass through untouched ─────────────────
print("F. multi-leg, mid-leg resume on a rally leg with rally values")
ep = {"completed_stages": [dict(STAGE_STATE, stage_index=i, tyre_compound=4)
                           for i in range(4)], "attempts_used": 0}
e = disp._multi_event_progress(
    115, layout=[3, 4], active=1, ep=ep,
    carry_over=True, prev_was_rx=False, target_is_rx=False)
check("State (rally resume untouched)", e["State"], 1)
check("(TyreCompound, TyresRemaining)", tyre(e), (4, 2))
check("VehicleMud present + round-trips", mud(e), EXPECTED_MUD)

# ── G. _leg_for_flat_index edge cases ──────────────────────────────────────
print("G. _leg_for_flat_index")
legs = [rally_leg(3), rx_leg(4)]
check("first leg", Dispatcher._leg_for_flat_index(legs, 0)["location"], RALLY_LOC)
check("last slot of first leg",
      Dispatcher._leg_for_flat_index(legs, 2)["location"], RALLY_LOC)
check("first slot of second leg",
      Dispatcher._leg_for_flat_index(legs, 3)["location"], RX_LOC)
check("past the end -> last leg",
      Dispatcher._leg_for_flat_index(legs, 99)["location"], RX_LOC)
check("empty legs", Dispatcher._leg_for_flat_index([], 0), {})

# ── H. legacy row without a stored mud snapshot -> zero-filled struct ──────
print("H. multi-leg, state entry has no vehicle_mud (legacy row)")
legacy_state = {k: v for k, v in STAGE_STATE.items() if k != "vehicle_mud"}
ep = {"completed_stages": [dict(legacy_state)], "attempts_used": 0}
e = disp._multi_event_progress(
    116, layout=[3, 4], active=0, ep=ep,
    carry_over=True, prev_was_rx=False, target_is_rx=True)
check("VehicleMud present, zero-filled", mud(e), {
    "Dirt": 0.0, "WheelMud0": 0.0, "WheelMud1": 0.0, "WheelMud2": 0.0,
    "WheelMud3": 0.0, "Mud": 0.0, "CleanHeight": 0.0,
    "CleanDirt": 0.0, "CleanMud": 0.0})

failed = CHECKS.count(False)
print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
sys.exit(1 if failed else 0)
