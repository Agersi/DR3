#!/usr/bin/env python3
"""Verify #63: streaming overlay state/files for all race types.

DataMining.DataEvent now feeds the dispatcher directly so official dailies and
time trials (which have no clubs_snapshot entry) still populate car / class /
location / stage files, while club races keep their event-snapshot labels.

Run: python tools/repro_streaming_all_race_types.py   (from D:\\CODE\\DR2)
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.account_store import AccountStore
from dr2server.dispatcher import RpcDispatcher
from dr2server.game_data import Track, VehicleClass, VEHICLES
from dr2server.streaming import FILES, StreamingWriter

FAILS = 0


def check(label, got, want):
    global FAILS
    ok = got == want
    print(f"[{'OK' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS += 1


def make_dispatcher(root: Path) -> RpcDispatcher:
    store = AccountStore(root / "accounts")
    d = RpcDispatcher(store)
    d.verbose_logging = False
    return d


def read_file(out_dir: Path, key: str):
    path = out_dir / FILES[key]
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def tick_writer(d: RpcDispatcher, out_dir: Path) -> StreamingWriter:
    writer = StreamingWriter(
        d, logger=lambda m: None, verbose=lambda: False
    )
    writer.set_output_dir(out_dir)
    writer._tick(1)
    return writer


def test_official_daily(tmp: Path) -> None:
    out = tmp / "official"
    d = make_dispatcher(tmp)

    # Stale club context from a previous race must not leak into an official
    # daily (no clubs_snapshot entry exists for it).
    d._current_event_id = "evt-stale"
    d._current_club_id = "club-stale"
    d._current_stage_ordinal = 7
    d._current_total_stages = 12
    d._current_stage_name = "Stale Stage"

    d.note_datamining_event(
        "StageStarted",
        {
            "challenge_type": 1,
            "stage_index": 0,
            "stage_count": 1,
            "track_model_id": 621,
            "location_id": 36,
            # StageStarted often carries zero vehicle fields.
            "vehicle_id": 0,
            "vehicle_class_id": 0,
        },
    )
    d.note_datamining_event(
        "DriveStarted",
        {
            "vehicle_id": 582,
            "vehicle_class_id": 86,
            "location_id": 36,
        },
    )

    state = d.get_streaming_state()
    check("official: stale club context cleared",
          (state["event_id"], state["club_id"]), (None, None))
    check("official: stage progress reset to the daily's own stage",
          (state["stage_ordinal"], state["total_stages"]), (0, 1))
    check("official: location resolved from Track/Location ids",
          state["location"], Track(621).location.display_name)
    check("official: stage name resolved from TrackModelId",
          state["stage_name"], Track(621).display_name)
    check("official: car class resolved from VehicleClassId",
          state["car_class"], VehicleClass(86).label)
    check("official: vehicle id carried", state["vehicle_id"], 582)

    tick_writer(d, out)
    check("official: Location.txt written",
          read_file(out, "location"), Track(621).location.display_name)
    check("official: Stage Name.txt written",
          read_file(out, "stage_name"), Track(621).display_name)
    check("official: Car Class.txt written",
          read_file(out, "car_class"), VehicleClass(86).label)
    check("official: Car.txt written",
          read_file(out, "car"), VEHICLES[582]["name"])
    check("official: Number of Stages.txt written",
          read_file(out, "stages"), "1 of 1")
    check("official: no Current Club.txt for a non-club race",
          read_file(out, "club"), None)
    check("official: no Leaderboard.txt (no club event id)",
          read_file(out, "leaderboard"), None)


def test_time_trial(tmp: Path) -> None:
    out = tmp / "timetrial"
    d = make_dispatcher(tmp)
    d._current_event_id = "evt-stale"
    d._current_club_id = "club-stale"

    d.note_datamining_event(
        "StageStarted",
        {
            "challenge_type": -1,
            "stage_index": 0,
            "stage_count": 1,
            "track_model_id": 621,
            "location_id": 36,
        },
    )
    d.note_datamining_event(
        "DriveStarted",
        {
            "vehicle_id": 582,
            "vehicle_class_id": 86,
            "location_id": 36,
        },
    )

    state = d.get_streaming_state()
    check("timetrial: stale club context cleared",
          (state["event_id"], state["club_id"]), (None, None))
    check("timetrial: location resolved",
          state["location"], Track(621).location.display_name)
    check("timetrial: stage name resolved",
          state["stage_name"], Track(621).display_name)

    tick_writer(d, out)
    check("timetrial: Location.txt written",
          read_file(out, "location"), Track(621).location.display_name)
    check("timetrial: Stage Name.txt written",
          read_file(out, "stage_name"), Track(621).display_name)
    check("timetrial: no Current Club.txt for a non-club race",
          read_file(out, "club"), None)
    check("timetrial: no Leaderboard.txt (no club event id)",
          read_file(out, "leaderboard"), None)


def test_club_snapshot_wins(tmp: Path) -> None:
    out = tmp / "club"
    d = make_dispatcher(tmp)

    # Stale direct labels from a previous official race must not override the
    # club event snapshot.
    d._current_location_name = "Stale Location"
    d._current_car_class_label = "Stale Class"
    d._current_event_id = "evt-1"
    d._current_club_id = "club-1"
    d._current_stage_ordinal = 3
    d._current_total_stages = 10
    d._current_stage_name = "Club Stage"
    d._clubs_snapshot = {
        "events": [
            {
                "id": "evt-1",
                "club_id": "club-1",
                "location": "Poland",
                "car_class": "Rally GT",
            }
        ],
        "clubs": [
            {
                "id": "club-1",
                "name": "Test Club",
                "created_by": "owner",
                "members": ["a", "b"],
            }
        ],
    }

    tick_writer(d, out)
    check("club: Current Club.txt from snapshot",
          read_file(out, "club"), "Test Club")
    check("club: Club Owner.txt from snapshot",
          read_file(out, "club_owner"), "owner")
    check("club: Number of members.txt from snapshot",
          read_file(out, "members"), "2 members")
    check("club: Location.txt from event snapshot (not stale direct label)",
          read_file(out, "location"), "Poland")
    check("club: Car Class.txt from event snapshot (not stale direct label)",
          read_file(out, "car_class"), "Rally GT")
    check("club: Stage Name.txt from club StageBegin state",
          read_file(out, "stage_name"), "Club Stage")
    check("club: Number of Stages.txt from championship progress",
          read_file(out, "stages"), "4 of 10")


def test_official_stage_begin_vehicle(tmp: Path) -> None:
    d = make_dispatcher(tmp)
    d.note_official_stage_begin({"VehicleId": 582})
    check("official StageBegin: vehicle id recorded for overlay",
          d.get_streaming_state()["vehicle_id"], 582)

    # A zero VehicleId must not clobber a known car.
    d.note_official_stage_begin({"VehicleId": 0})
    check("official StageBegin: zero VehicleId does not clobber",
          d.get_streaming_state()["vehicle_id"], 582)


def test_ignored_events(tmp: Path) -> None:
    d = make_dispatcher(tmp)
    before = d.get_streaming_state()
    d.note_datamining_event(
        "AppStarted",
        {
            "vehicle_id": 582,
            "vehicle_class_id": 86,
            "location_id": 36,
        },
    )
    after = d.get_streaming_state()
    check("non-overlay DataEvent ignored", after, before)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dr3-streaming-") as tmp:
        root = Path(tmp)
        test_official_daily(root / "official-root")
        test_time_trial(root / "timetrial-root")
        test_club_snapshot_wins(root / "club-root")
        test_official_stage_begin_vehicle(root / "stage-begin-root")
        test_ignored_events(root / "ignored-root")

    if FAILS:
        print(f"FAILED ({FAILS} check(s))")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
