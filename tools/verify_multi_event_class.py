"""Offline check: what does the game receive when a multi-location rally
advances to its next event (and when a mixed-class Rally Season leg opens)?

Each leg is served as its own single-event Challenge with a distinct
ChallengeID, and this script prints exactly what that challenge carries —
class Requirement, EntryWindow, and the Progress entries under the new
ChallengeID at the moment it becomes active.  A fresh ChallengeID with NO
Progress entry is what DR2 shows the car select screen for; a State=1
Progress entry seeded with a vehicle state makes the game resume into that
car instead.  Multi-Location Rally legs carry the previous leg's vehicle
state over (one car-select at the start of the championship); Rally Season
legs stay independent.

The game client treats each served event as a standalone single-event
championship (per protocol notes from upstream captures).  The in-game
screens themselves still need one real playthrough to confirm.

Run: python tools\\verify_multi_event_class.py   (from D:\\CODE\\DR2)
"""
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.dispatcher import RpcDispatcher  # noqa: E402
from dr2server.game_data import Location, Track, Vehicle, VehicleClass  # noqa: E402

CLUB_M = "club-verify-multi"
EVENT_M = "evt-verify-multi"   # multi-location rally, layout [1,1], one class
CLUB_S = "club-verify-season"
EVENT_S = "evt-verify-season"  # season, leg0 Group B -> leg1 RX Supercars

GROUP_B = int(VehicleClass.GROUP_B_4WD)
RX_SUPERCARS = int(VehicleClass.RX_SUPERCARS)


def _stage(track):
    return {"track_id": int(track), "conditions_id": 1,
            "surface_deg": None, "service_area": None}


# A completed stage as the web my-progress endpoint reports it, carrying the
# full vehicle state the player finished the stage with.
def _done_stage(stage_index, time_ms, vehicle_id, livery_id):
    return {
        "stage_index": stage_index, "time_ms": time_ms, "penalties_ms": 0,
        "meters_driven": 4200, "distance_driven": 4200,
        "vehicle_id": vehicle_id, "livery_id": livery_id,
        "nationality_id": 0,
        "has_repaired": True, "repair_penalty_ms": 1250,
        "tuning_setup_b64": "",
        "tyre_compound": 7, "tyres_remaining": 1,
        "vehicle_damage": {"wheels_wear": 35, "turbo": 0, "springs": 0,
                           "radiator": 0.42, "lights": 0.0, "gearbox": 0},
    }


EVENT_M_D = {
    "id": EVENT_M, "club_id": CLUB_M, "name": "Verify Multi",
    "active": True, "car_class": "Group B 4WD", "mode": "multi",
    "settings": {},
    "start_time": "2026-10-01T00:00:00", "end_time": "2027-01-01T00:00:00",
    "events": [
        {"location": "Poland", "car_class": "Group B 4WD",
         "stages": [_stage(Track.JEZIORO_ROTCZE)]},
        {"location": "Germany", "car_class": "Group B 4WD",
         "stages": [_stage(Track.VERBUNDSRING_REVERSE)]},
    ],
}

# Season: leg 0 (Group B, 60 min) already over, leg 1 (RX Supercars, 60 min)
# active right now — start = now - 65 min.
_START = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=65)
EVENT_S_D = {
    "id": EVENT_S, "club_id": CLUB_S, "name": "Verify Season",
    "active": True, "car_class": "Group B 4WD", "mode": "season",
    "settings": {},
    "start_time": _START.isoformat(),
    "end_time": (_START + timedelta(hours=2)).isoformat(),
    "events": [
        {"location": "Germany", "car_class": "Group B 4WD",
         "duration": {"days": 0, "hours": 1, "mins": 0},
         "stages": [_stage(Track.VERBUNDSRING_REVERSE)]},
        {"location": "Estering", "car_class": "RX Supercars",
         "duration": {"days": 0, "hours": 1, "mins": 0},
         "stages": [_stage(Track.ESTERING)]},
    ],
}


class FakeClient:
    def __init__(self, progress_events):
        self.progress_events = progress_events

    def get_clubs(self):
        return {
            "clubs": [
                {"id": CLUB_M, "name": "Verify Multi Club", "created_by": "Agersi"},
                {"id": CLUB_S, "name": "Verify Season Club", "created_by": "Agersi"},
            ],
            "events": [EVENT_M_D, EVENT_S_D],
        }

    def resolve_location_id(self, name):
        try:
            return int(getattr(Location, name.upper()))
        except AttributeError:
            return None

    def tracks_for_location(self, lid):
        by_loc = {
            int(Location.POLAND): [int(Track.JEZIORO_ROTCZE)],
            int(Location.GERMANY): [int(Track.VERBUNDSRING_REVERSE)],
            int(Location.ESTERING): [int(Track.ESTERING)],
        }
        return by_loc.get(lid, [])

    def resolve_vclass_id(self, label):
        # Same canonical resolver the real api_client uses.
        from dr2server.game_data import vehicle_class_id_for_label
        return vehicle_class_id_for_label(label)

    def get_event(self, eid):
        if eid == EVENT_M:
            return EVENT_M_D
        if eid == EVENT_S:
            return EVENT_S_D
        return None

    def get_leaderboard(self, eid):
        return []

    def get_my_progress(self):
        return {"ok": True, "events": self.progress_events}

    def test_token(self):
        return "Agersi"


def _val(x):
    return x.value if hasattr(x, "value") else x


def req_class(chal):
    for r in chal.get("Requirements", []):
        if r.get("Type") == 1:
            return int(_val(r["Value"]))
    return None


def show(title, resp, club_name, chal_name, expect_chal_id, expect_class,
         expect_progress, expect_state=None, expect_stage_index=None,
         expect_vehicle_id=None):
    print(f"\n=== {title} ===")
    club = next((c for c in resp.get("Clubs", [])
                 if c.get("Name") == club_name), None)
    chal = next((c for c in resp.get("Challenges", [])
                 if c.get("Name") == chal_name), {})
    cid = chal.get("ChallengeID")
    cls = req_class(chal)
    prog = [p for p in resp.get("Progress", [])
            if p.get("ChallengeID") == cid]
    ok = True
    print(f"  club EventIndex={club.get('EventIndex') if club else None} "
          f"AmountOfEvents={club.get('AmountOfEvents') if club else None}")
    print(f"  ChallengeID={cid} class Requirement={cls} "
          f"(GroupB={GROUP_B}, RXSupercars={RX_SUPERCARS})")
    ew = chal.get("EntryWindow", {})
    if ew:
        print(f"  EntryWindow start={_val(ew.get('Start'))} "
              f"end={_val(ew.get('End'))} (now={int(time.time())})")
    for p in prog:
        print(f"  Progress: State={p.get('State')} StageIndex={p.get('StageIndex')} "
              f"VehicleId={_val(p.get('VehicleId'))} LiveryId={_val(p.get('LiveryId'))} "
              f"HasRepaired={p.get('HasRepaired')} RepairPenalty="
              f"{_val(p.get('RepairPenalty'))} TyreCompound="
              f"{_val(p.get('TyreCompound'))} TyresRemaining="
              f"{_val(p.get('TyresRemaining'))}")
        dmg = p.get("VehicleDamage") or {}
        print(f"          VehicleDamage radiator={dmg.get('Radiator')} "
              f"wheels_wear={_val(dmg.get('WheelsWear'))} "
              f"TuningSetup={len(p.get('TuningSetup') or b'')} bytes")
    if not prog:
        print("  Progress entries under this ChallengeID: 0 (none)")
    if expect_chal_id is not None and cid != expect_chal_id:
        ok = False
        print(f"  FAIL: expected ChallengeID {expect_chal_id}")
    if expect_class is not None and cls != expect_class:
        ok = False
        print(f"  FAIL: expected class Requirement {expect_class}")
    if expect_progress is not None and len(prog) != expect_progress:
        ok = False
        print(f"  FAIL: expected {expect_progress} progress entries")
    if prog:
        p0 = prog[0]
        if expect_state is not None and p0.get("State") != expect_state:
            ok = False
            print(f"  FAIL: expected Progress State {expect_state}")
        if expect_stage_index is not None \
                and p0.get("StageIndex") != expect_stage_index:
            ok = False
            print(f"  FAIL: expected Progress StageIndex {expect_stage_index}")
        if expect_vehicle_id is not None \
                and _val(p0.get("VehicleId")) != expect_vehicle_id:
            ok = False
            print(f"  FAIL: expected carried VehicleId {expect_vehicle_id}")
    return ok


def main():
    # ── Multi-location rally, fresh (no completed stages) ──────────────
    d1 = RpcDispatcher(account_store=None, api_client=FakeClient([]))
    resp1 = d1.dispatch("Clubs.GetClubs", {})
    base_m = None
    for c in resp1.get("Challenges", []):
        if c.get("Name") == "Verify Multi":
            base_m = c.get("ChallengeID")
    ok1 = show("MULTI fresh — leg 0 active", resp1, "Verify Multi Club",
               "Verify Multi",
               expect_chal_id=base_m, expect_class=GROUP_B,
               expect_progress=0)

    # ── Multi-location rally, leg 0 complete -> leg 1 becomes active ───
    delta = int(Vehicle.LANCIA_DELTA_S4)
    d2 = RpcDispatcher(account_store=None, api_client=FakeClient([
        {"event_id": EVENT_M,
         "completed_stages": [_done_stage(0, 300000, delta, 2915)],
         "total_time_ms": 300000, "attempts_used": 0}]))
    resp2 = d2.dispatch("Clubs.GetClubs", {})
    ok2 = show("MULTI after leg 0 complete — leg 1 active (NEW ChallengeID)",
               resp2, "Verify Multi Club", "Verify Multi",
               expect_chal_id=base_m + 1 if base_m else None,
               expect_class=GROUP_B, expect_progress=1,
               expect_state=1, expect_stage_index=0,
               expect_vehicle_id=delta)

    # ── Rally Season, mixed classes, leg 1 (RX Supercars) active ───────
    # Leg 0 is already complete: season legs must NOT carry the car over.
    quattro = int(Vehicle.AUDI_SPORT_QUATTRO_S1_E2)
    d3 = RpcDispatcher(account_store=None, api_client=FakeClient([
        {"event_id": EVENT_S,
         "completed_stages": [_done_stage(0, 250000, quattro, 2914)],
         "total_time_ms": 250000, "attempts_used": 0}]))
    resp3 = d3.dispatch("Clubs.GetClubs", {})
    ok3 = show("SEASON mixed classes — leg 1 (RX Supercars) active now",
               resp3, "Verify Season Club", "Verify Season",
               expect_chal_id=None, expect_class=RX_SUPERCARS,
               expect_progress=0)

    print()
    if ok1 and ok2 and ok3:
        print("ALL OFFLINE CHECKS PASSED")
        print("""
Interpretation:
- Multi fresh leg 0: NEW ChallengeID, no Progress -> car select screen.
- Multi after leg 0 complete: the game receives a NEW ChallengeID (same class
  Requirement) WITH one State=1 Progress entry seeded from leg 0's final
  vehicle state (car/livery/damage/tyres/tuning carried over).  A Progress
  entry is what makes DR2 resume into that car instead of showing the car
  select screen, so multi-location rallies keep the chosen car between legs.
- Season leg 1 carries RX Supercars (78), not the first leg's Group B —
  per-leg classes are served correctly — and gets NO carried Progress entry
  even though leg 0 is complete: season rallies stay independent.
The actual on-screen confirmation still needs one in-game playthrough.""")
    else:
        print("SOME CHECKS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
