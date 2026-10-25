r"""Verify backlog #13 probe: settings.vehicle_ids -> Type=0 vehicle requirements.

Unit-level checks against RpcDispatcher._vehicle_requirements and the full
_challenge_egonet output (Requirements + UseInvVehicle), mirroring how upstream
EGO single-car challenges encode their restriction (T0=<VehicleId>).

Run: python tools\repro_vehicle_reqs.py   (from D:\CODE\DR2)
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore
from dr2server.dispatcher import RpcDispatcher
from dr2server.egonet import UInt32

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
    store = AccountStore(Path(r"D:\CODE\DR2\build\vehicle_reqs_test_accounts") / "u")
    return RpcDispatcher(store, api_client=None)


def reqs_of(wevt):
    """_vehicle_requirements result with egonet wrappers unwrapped."""
    r = make_dispatcher()._vehicle_requirements(wevt)
    if r is None:
        return None
    return [(unwrap(x["Type"]), unwrap(x["Value"])) for x in r]


def main():
    # 1-3. unset / empty / non-list -> None (callers keep the class requirement)
    check("unset settings", reqs_of({"id": "a"}), None)
    check("empty list", reqs_of({"id": "a", "settings": {"vehicle_ids": []}}), None)
    check("non-list", reqs_of({"id": "a", "settings": {"vehicle_ids": "575"}}), None)

    # 4. unconfirmed vehicle id -> None (invalid ids crash the game client)
    check("unconfirmed id", reqs_of({"id": "a", "settings": {"vehicle_ids": [999999]}}), None)
    check("mixed valid+invalid",
          reqs_of({"id": "a", "settings": {"vehicle_ids": [575, 999999]}}), None)

    # 5. single car -> one Type=0 requirement (upstream EGO shape)
    check("single car 575 (BMW M1 Procar Rally)",
          reqs_of({"id": "a", "settings": {"vehicle_ids": [575]}}), [(0, 575)])

    # 6. two cars, same class (Group B RWD)
    check("two cars same class",
          reqs_of({"id": "a", "settings": {"vehicle_ids": [575, 480]}}),
          [(0, 575), (0, 480)])

    # 7. two cars from different classes (F2 Kit Car + Group A)
    check("two cars cross-class",
          reqs_of({"id": "a", "settings": {"vehicle_ids": [483, 536]}}),
          [(0, 483), (0, 536)])

    # 8. full _challenge_egonet with vehicle_ids -> T0 requirements + UseInvVehicle
    d = make_dispatcher()
    wevt = {"id": "a", "name": "Test",
            "settings": {"vehicle_ids": [575, 480]},
            "start_time": "2026-10-25T00:00:00",
            "end_time": "2026-10-26T00:00:00"}
    vreqs = d._vehicle_requirements(wevt)
    out = d._challenge_egonet(wevt, 200001, 2001,
                              vreqs if vreqs is not None
                              else [{"Type": 1, "Value": UInt32(74)}],
                              [], 0, "Club",
                              use_inv_vehicle=d._use_inv_vehicle(wevt, vreqs))
    check("egonet Requirements (vehicle_ids set)",
          [(unwrap(r["Type"]), unwrap(r["Value"])) for r in out["Requirements"]],
          [(0, 575), (0, 480)])
    check("egonet UseInvVehicle (vehicle_ids set)", out["UseInvVehicle"], True)

    # 8b. settings.use_inv_vehicle override (isolation-test knob): explicit bool
    # wins over the default; non-bool values are ignored.
    check("override false with vehicle_ids",
          d._use_inv_vehicle({"settings": {"vehicle_ids": [575],
                                           "use_inv_vehicle": False}},
                             [(0, 575)]), False)
    check("override true without vehicle_ids",
          d._use_inv_vehicle({"settings": {"use_inv_vehicle": True}}, None), True)
    check("non-bool override ignored (vehicle_ids set)",
          d._use_inv_vehicle({"settings": {"vehicle_ids": [575],
                                           "use_inv_vehicle": "false"}},
                             [(0, 575)]), True)
    check("non-bool override ignored (class fallback)",
          d._use_inv_vehicle({"settings": {"use_inv_vehicle": 0}}, None), False)

    # 9. full _challenge_egonet without vehicle_ids -> class requirement, no inv flag
    wevt2 = {"id": "b", "name": "Test2", "settings": {},
             "start_time": "2026-10-25T00:00:00",
             "end_time": "2026-10-26T00:00:00"}
    out2 = d._challenge_egonet(wevt2, 200002, 2001,
                               [{"Type": 1, "Value": UInt32(74)}], [], 0, "Club")
    check("egonet Requirements (class fallback)",
          [(unwrap(r["Type"]), unwrap(r["Value"])) for r in out2["Requirements"]],
          [(1, 74)])
    check("egonet UseInvVehicle (class fallback)", out2["UseInvVehicle"], False)

    # 10. ChallengeType/ScoringType/Mode overrides (ChallengeType=1 experiment):
    # int settings.challenge_type / scoring_type / challenge_mode replace the
    # club defaults (2/2/1); a string championship 'mode' must be ignored.
    wevt3 = {"id": "c", "name": "Test3",
             "settings": {"challenge_type": 1, "scoring_type": 0,
                          "challenge_mode": 0},
             "start_time": "2026-10-25T00:00:00",
             "end_time": "2026-10-26T00:00:00"}
    out3 = d._challenge_egonet(wevt3, 200003, 2001,
                               [{"Type": 1, "Value": UInt32(74)}], [], 0, "Club")
    check("egonet ChallengeType override", out3["ChallengeType"], 1)
    check("egonet ScoringType override", out3["ScoringType"], 0)
    check("egonet Mode override", unwrap(out3["Mode"]), 0)

    # 11. no overrides -> club defaults preserved (2/2/1)
    check("egonet ChallengeType default", out2["ChallengeType"], 2)
    check("egonet ScoringType default", out2["ScoringType"], 2)
    check("egonet Mode default", unwrap(out2["Mode"]), 1)

    # 12. non-int override values ignored (championship 'mode' is a string)
    wevt4 = {"id": "d", "name": "Test4",
             "settings": {"challenge_mode": "multi"},
             "start_time": "2026-10-25T00:00:00",
             "end_time": "2026-10-26T00:00:00"}
    out4 = d._challenge_egonet(wevt4, 200004, 2001,
                               [{"Type": 1, "Value": UInt32(74)}], [], 0, "Club")
    check("egonet Mode non-int ignored", unwrap(out4["Mode"]), 1)

    print()
    if FAILS:
        print(f"{FAILS} CHECK(S) FAILED")
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
