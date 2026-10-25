r"""Verify item #17 fix: RX-before-rally tyre carry-over in Multi-Location Rally.

Unit-level checks against RpcDispatcher._multi_event_progress / _leg_is_rx:
  1. _leg_is_rx detects rally vs RX legs by location name.
  2. Seed after an RX leg (prev_was_rx=True) -> TyreCompound=7, TyresRemaining=2
     (upstream defaults), car/livery/damage still carried over.
  3. Rally->rally seed (prev_was_rx=False) -> stored tyre values carried unchanged.
  4. Season mode (carry_over=False) unstarted leg -> None.

Run: python tools\repro_rx_tyre_carry.py   (from D:\CODE\DR2)
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore
from dr2server.api_client import UpstreamClient
from dr2server.dispatcher import RpcDispatcher

FAILS = 0


def unwrap(v):
    return v.value if hasattr(v, "value") else v


def check(label, got, want):
    global FAILS
    ok = got == want
    print(f"[{'OK' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS += 1


def make_dispatcher():
    store = AccountStore(Path(r"D:\CODE\DR2\build\rx_tyre_test_accounts") / "u")
    return RpcDispatcher(store, api_client=UpstreamClient())


def stage(compound, tyres, vehicle_id=469, livery_id=3233):
    return {
        "vehicle_id": vehicle_id,
        "livery_id": livery_id,
        "tyre_compound": compound,
        "tyres_remaining": tyres,
        "has_repaired": False,
        "repair_penalty_ms": 0,
        "vehicle_damage": {"bodywork": 12, "engine": 3.5},
        "tuning_setup_b64": "",
    }


def main():
    d = make_dispatcher()
    wevt = {"id": "evt-test", "location": ""}

    # 1) _leg_is_rx detection
    check("_leg_is_rx germany (rally)", d._leg_is_rx(wevt, {"location": "germany"}), False)
    check("_leg_is_rx spain (rally)", d._leg_is_rx(wevt, {"location": "spain"}), False)
    check("_leg_is_rx holjes (RX)", d._leg_is_rx(wevt, {"location": "holjes"}), True)
    check("_leg_is_rx mettet (RX)", d._leg_is_rx(wevt, {"location": "mettet"}), True)
    check("_leg_is_rx empty loc", d._leg_is_rx(wevt, {"location": ""}), False)
    check("_leg_is_rx unknown loc", d._leg_is_rx(wevt, {"location": "nowhere"}), False)

    # 2) RX -> rally: seed must use upstream tyre defaults (7/2); car/damage carried.
    #    layout [1,1,1], active leg 2, legs 0 (rally) + 1 (RX) complete.
    ep_rx = {"completed_stages": [stage(1, 1), stage(13, 0)]}
    p = d._multi_event_progress(
        challenge_id=999, layout=[1, 1, 1], active=2, ep=ep_rx,
        carry_over=True, prev_was_rx=True,
    )
    check("RX->rally seed is not None", p is not None, True)
    if p is not None:
        check("RX->rally State", unwrap(p["State"]), 1)
        check("RX->rally StageIndex", unwrap(p["StageIndex"]), 0)
        check("RX->rally VehicleId", unwrap(p["VehicleId"]), 469)
        check("RX->rally LiveryId", unwrap(p["LiveryId"]), 3233)
        check("RX->rally TyreCompound (was 13 from RX)", unwrap(p["TyreCompound"]), 7)
        check("RX->rally TyresRemaining (was 0 from RX)", unwrap(p["TyresRemaining"]), 2)
        check("RX->rally Bodywork carried", unwrap(p["VehicleDamage"]["Bodywork"]), 12)
        check("RX->rally Engine carried", p["VehicleDamage"]["Engine"], 3.5)

    # 3) Rally -> rally: stored tyre values carry over unchanged.
    ep_rally = {"completed_stages": [stage(1, 1), stage(1, 3)]}
    p2 = d._multi_event_progress(
        challenge_id=999, layout=[1, 1, 1], active=2, ep=ep_rally,
        carry_over=True, prev_was_rx=False,
    )
    check("rally->rally seed is not None", p2 is not None, True)
    if p2 is not None:
        check("rally->rally TyreCompound carried", unwrap(p2["TyreCompound"]), 1)
        check("rally->rally TyresRemaining carried", unwrap(p2["TyresRemaining"]), 3)

    # 4) Season mode (carry_over=False): unstarted leg -> None.
    p3 = d._multi_event_progress(
        challenge_id=999, layout=[1, 1], active=1,
        ep={"completed_stages": [stage(1, 1)]},
        carry_over=False, prev_was_rx=False,
    )
    check("season unstarted leg -> None", p3, None)

    print()
    if FAILS:
        print(f"{FAILS} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
