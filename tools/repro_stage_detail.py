#!/usr/bin/env python3
"""Verify the per-stage run detail buffer (dr2server/stage_detail.py).

The game reports every finished stage twice on independent channels (the
StageEnded telemetry snapshot + the StageComplete RPC) whose arrival order is
not guaranteed. The buffer must pair them on (challenge_id, event_index,
stage_index), emit one merged record when both halves are in, and timeout-flush
whatever is pending when only one half ever arrives (DNF close-outs).

Run: python tools/repro_stage_detail.py   (from D:\\CODE\\DR2)
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from dr2server.stage_detail import StageDetailBuffer

FAILS = 0


def check(label, got, want):
    global FAILS
    ok = got == want
    print(f"[{'OK' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS += 1


class Collector:
    def __init__(self):
        self.records = []
        self._lock = threading.Lock()

    def send(self, record):
        with self._lock:
            self.records.append(record)

    def wait(self, n, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if len(self.records) >= n:
                    return True
            time.sleep(0.02)
        with self._lock:
            return len(self.records) >= n


def make_buffer(timeout=15.0):
    col = Collector()
    buf = StageDetailBuffer(col.send)
    buf.FLUSH_TIMEOUT_S = timeout
    return buf, col


DM_STAGE0 = {
    "challenge_id": 900001, "event_index": 0, "stage_index": 0,
    "challenge_type": 2, "track_id": 636, "track_model_id": 636,
    "location_id": 4, "vehicle_class_id": 97, "vehicle_id": 565,
    "player_finished": True, "player_end_status": 0, "race_position": 1,
    "stage_time_seconds": 155.99,
    "weather_preset_id": 3, "surface_degradation_enabled": False,
    "surface_degradation_level": 0.0, "is_hardcore_damage_enabled": True,
    "is_any_assists_enabled": False, "tyre_compound_id": 6,
    "tyre_wear_state": 1, "vr_enabled": False,
    "metres_cam_bonnet": 48210.5, "metres_cam_bumper": 0.0,
    "metres_cam_chase_close": 0.0, "metres_cam_chase_far": 0.0,
    "metres_cam_dash": 0.0, "metres_cam_head": 0.0,
    "library_id": "lib_direct_input",
    "device_hardware_id": "{GUID-1234}", "driver_name": "Simucube 2 Pro",
    "g_separate_days_played_count": 12, "g_total_seconds_played": 43200,
    "g_session_count": 7,
}

RPC_STAGE0 = {
    "challenge_id": 900001, "event_index": 0, "stage_index": 0,
    "vehicle_id": 565, "livery_id": 3813, "stage_time_ms": 155990,
    "race_status": 0, "recov_to_service": False,
    "meters_driven": 48210, "distance_driven": 48210,
    "using_wheel": True, "using_assists": False,
    "event_id": "evt-abc",
}


def test_rpc_first():
    buf, col = make_buffer()
    buf.merge_rpc((900001, 0, 0), RPC_STAGE0)
    check("rpc-first: nothing emitted after rpc half", len(col.records), 0)
    buf.feed_datamining(DM_STAGE0)
    ok = col.wait(1)
    check("rpc-first: merged record emitted", ok and len(col.records), True)
    r = col.records[0] if ok else {}
    check("rpc-first: dm half present (device name)",
          r.get("driver_name"), "Simucube 2 Pro")
    check("rpc-first: rpc half present (event id)", r.get("event_id"), "evt-abc")
    check("rpc-first: exact ms wins over snapshot seconds",
          r.get("stage_time_ms"), 155990)
    check("rpc-first: source ids kept",
          (r.get("challenge_id"), r.get("event_index"), r.get("stage_index")),
          (900001, 0, 0))


def test_dm_first():
    buf, col = make_buffer()
    buf.feed_datamining(DM_STAGE0)
    check("dm-first: nothing emitted after dm half", len(col.records), 0)
    buf.merge_rpc((900001, 0, 0), RPC_STAGE0)
    ok = col.wait(1)
    check("dm-first: merged record emitted", ok and len(col.records), True)
    r = col.records[0] if ok else {}
    check("dm-first: wheel flag from rpc", r.get("using_wheel"), True)
    check("dm-first: distance from rpc ints", r.get("distance_driven"), 48210)
    check("dm-first: camera metres kept", r.get("metres_cam_bonnet"), 48210.5)


def test_timeout_flush_dm_only():
    buf, col = make_buffer(timeout=0.3)
    dm = dict(DM_STAGE0, challenge_id=900002, stage_index=3)
    buf.feed_datamining(dm)
    ok = col.wait(1, timeout=3.0)
    check("timeout: dm-only record flushed after timeout",
          ok and len(col.records), True)
    r = col.records[0] if ok else {}
    check("timeout: rpc fields absent", "stage_time_ms" in r, False)
    check("timeout: dm fields present", r.get("driver_name"), "Simucube 2 Pro")


def test_timeout_flush_rpc_only():
    buf, col = make_buffer(timeout=0.3)
    rpc = dict(RPC_STAGE0, challenge_id=900003, stage_index=1)
    buf.merge_rpc((900003, 0, 1), rpc)
    ok = col.wait(1, timeout=3.0)
    check("timeout: rpc-only record flushed after timeout",
          ok and len(col.records), True)
    r = col.records[0] if ok else {}
    check("timeout: dm fields absent", "driver_name" in r, False)
    check("timeout: rpc fields present", r.get("using_wheel"), True)


def test_dnf_shape():
    buf, col = make_buffer()
    dm = dict(DM_STAGE0, challenge_id=900004, player_finished=False,
              player_end_status=5, race_position=0, stage_time_seconds=0.0)
    rpc = dict(RPC_STAGE0, challenge_id=900004, stage_time_ms=0,
               race_status=5, recov_to_service=True,
               meters_driven=1234, distance_driven=1234)
    buf.feed_datamining(dm)
    buf.merge_rpc((900004, 0, 0), rpc)
    ok = col.wait(1)
    check("dnf: merged record emitted", ok and len(col.records), True)
    r = col.records[0] if ok else {}
    check("dnf: race status carried", r.get("race_status"), 5)
    check("dnf: not finished flag carried", r.get("player_finished"), False)
    check("dnf: distance kept (partial run)", r.get("distance_driven"), 1234)


def test_no_cross_contamination():
    buf, col = make_buffer()
    dm0 = dict(DM_STAGE0, challenge_id=900005)
    rpc1 = dict(RPC_STAGE0, challenge_id=900005, stage_index=1,
                stage_time_ms=200000)
    buf.feed_datamining(dm0)          # stage 0 dm half
    buf.merge_rpc((900005, 0, 1), rpc1)  # stage 1 rpc half (different key)
    check("cross: no emit while halves are on different keys",
          len(col.records), 0)
    buf.merge_rpc((900005, 0, 0), RPC_STAGE0)   # completes stage 0
    buf.feed_datamining(dict(DM_STAGE0, challenge_id=900005,
                             stage_index=1))     # completes stage 1
    ok = col.wait(2)
    check("cross: two separate records", ok and len(col.records), 2)
    if ok:
        by_stage = {r.get("stage_index"): r for r in col.records}
        check("cross: stage0 record has its own time",
              by_stage.get(0, {}).get("stage_time_ms"), 155990)
        check("cross: stage1 record has its own time",
              by_stage.get(1, {}).get("stage_time_ms"), 200000)


def test_duplicate_half():
    buf, col = make_buffer()
    buf.feed_datamining(DM_STAGE0)
    buf.feed_datamining(dict(DM_STAGE0, driver_name="Updated Wheel"))
    check("dup: still nothing before rpc", len(col.records), 0)
    buf.merge_rpc((900001, 0, 0), RPC_STAGE0)
    ok = col.wait(1)
    check("dup: exactly one record after rpc", ok and len(col.records), 1)
    r = col.records[0] if ok else {}
    check("dup: newer dm half wins", r.get("driver_name"), "Updated Wheel")


def test_emit_failure_isolated():
    def boom(record):
        raise RuntimeError("web down")

    buf = StageDetailBuffer(boom)
    try:
        buf.feed_datamining(DM_STAGE0)
        buf.merge_rpc((900001, 0, 0), RPC_STAGE0)
        check("emit-failure: no exception on request path", True, True)
    except Exception as e:
        check(f"emit-failure: no exception on request path ({e!r})", False, True)
    time.sleep(0.2)


def test_club_path_flat_index():
    """Club StageComplete feed (#58c): the RPC half fed by dispatcher.
    _stage_complete carries event_id + flat_stage_index (championship-wide
    ordinal via the leg offset) + telemetry splits, so the merged row
    correlates with club_results.stages_json."""
    from dr2server.account_store import AccountStore
    from dr2server.api_client import _location_id_for
    from dr2server.dispatcher import RpcDispatcher

    def stage(idx):
        return {"stage_index": idx, "time_ms": 0, "meters_driven": 0,
                "vehicle_id": 565, "livery_id": 3813}

    class FakeApi:
        def get_my_progress(self):
            return {"events": [{"event_id": "evtC", "completed_stages": [],
                                "total_time_ms": 0, "attempts_used": 0}]}

        def get_event(self, event_id):
            return {"events": [{"stages": [stage(0), stage(1)]},
                               {"stages": [stage(2), stage(3)]}]}

        def resolve_location_id(self, name):
            return _location_id_for(name)

        def submit_stage_begin(self, **kw):
            pass

        def submit_stage(self, **kw):
            return {"ok": True}

    store = AccountStore(Path(r"D:\CODE\DR2\build\stage_detail_scratch") / "u")
    d = RpcDispatcher(store, api_client=FakeApi())
    col = Collector()
    buf = StageDetailBuffer(col.send)
    buf.FLUSH_TIMEOUT_S = 0.3
    d.stage_detail = buf
    d._challenge_event_map = {900010: "evtC"}
    d._challenge_subevent_map = {900010: 1}   # leg 1 -> flat offset 2
    d._compute_stage_splits = lambda *a, **k: [41.871, 41.021]

    params = {"VehicleId": 565, "VehicleInstId": -4, "LiveryId": 3813,
              "StageIndex": 0, "EventIndex": 0, "ChallengeId": 900010,
              "NationalityId": 31, "StageTime": 155.99,
              "MetersDriven": 48210, "DistanceDriven": 48210,
              "RaceStatus": 0, "RecovToService": False}
    d._stage_complete(params)
    ok = col.wait(1, timeout=3.0)   # rpc-only half flushes on timeout
    check("club: rpc half fed to the buffer", ok and len(col.records), True)
    r = col.records[0] if ok else {}
    check("club: event id carried", r.get("event_id"), "evtC")
    check("club: flat index = leg offset + stage index",
          r.get("flat_stage_index"), 2)
    check("club: telemetry splits pass through",
          r.get("splits"), [41.871, 41.021])

    # DNF close-out: finished_cleanly is False -> no splits, no flat index.
    col2 = Collector()
    buf2 = StageDetailBuffer(col2.send)
    buf2.FLUSH_TIMEOUT_S = 0.3
    d.stage_detail = buf2
    p2 = dict(params, RaceStatus=5)
    d._stage_complete(p2)
    ok2 = col2.wait(1, timeout=3.0)
    check("club DNF: rpc half fed too", ok2 and len(col2.records), True)
    r2 = col2.records[0] if ok2 else {}
    check("club DNF: splits not computed", r2.get("splits"), None)
    check("club DNF: flat index not computed",
          r2.get("flat_stage_index"), None)


def test_httpd_wiring_e2e():
    """Full wiring: a real App against a local capture server — the StageEnded
    vdic through RequestHandler._log_datamining_event + the StageComplete RPC
    through _record_proxy_stats must merge on the same key and POST to
    /api/game/stage-detail with both halves."""
    import json as _json
    import types
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from dr2server import httpd
    from dr2server.egonet import Int64, UInt32

    posts = []

    class _Cap(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(n)
            if self.path == '/api/game/stage-detail':
                posts.append(_json.loads(body))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(('127.0.0.1', 0), _Cap)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    scratch = Path(r"D:\CODE\DR2\build\stage_detail_scratch")
    app = httpd.App(
        data_root=scratch, capture_root=scratch / "captures",
        upstream_ip="159.153.126.42", proxy_default_except_clubs=True,
        api_url=f"http://127.0.0.1:{port}", api_token="df_test")
    check("wiring: buffer created + attached to dispatcher",
          app.stage_detail is not None
          and app.dispatcher.stage_detail is app.stage_detail, True)

    handler = httpd.RequestHandler.__new__(httpd.RequestHandler)
    handler.server = types.SimpleNamespace(app=app)

    # StageEnded snapshot (columnar vdic shape, values wrapped like the wire).
    dm_payload = {"Statistics": {"Data": {
        "event": "StageEnded",
        "keys": ["challenge_id", "event_index", "stage_index", "challenge_type",
                 "track_id", "location_id", "vehicle_class_id", "vehicle_id",
                 "player_finished", "race_position", "stage_time_seconds",
                 "weather_preset_id", "stage_conditions_id",
                 "library_id", "driver_name",
                 "g_separate_days_played_count", "g_total_seconds_played",
                 "g_session_count"],
        "values": [Int64(910001), UInt32(0), UInt32(2), Int64(1),
                   UInt32(636), UInt32(4), UInt32(97), UInt32(565),
                   True, UInt32(1), 155.99,
                   UInt32(8), UInt32(26),
                   "lib_direct_input", "Simucube 2 Pro",
                   Int64(12), Int64(43200), Int64(7)],
    }}}
    handler._log_datamining_event(dm_payload)

    # StageComplete RPC (official/TT proxied path — wrapped values).
    rpc_payload = {
        "ChallengeId": Int64(910001), "EventIndex": UInt32(0),
        "StageIndex": UInt32(2),
        "VehicleId": UInt32(565), "LiveryId": UInt32(3813),
        "StageTime": 155.99, "RaceStatus": UInt32(0), "RecovToService": False,
        "MetersDriven": Int64(48210), "DistanceDriven": Int64(48210),
        "UsingWheel": True, "UsingAssists": False,
    }
    handler._record_proxy_stats("RaceNetChallenges.StageComplete", rpc_payload)

    deadline = time.time() + 5.0
    while not posts and time.time() < deadline:
        time.sleep(0.05)
    check("wiring: exactly one merged POST to /api/game/stage-detail",
          len(posts), 1)
    if posts:
        p = posts[0]
        check("wiring: dm half in the post (device name)",
              p.get("driver_name"), "Simucube 2 Pro")
        check("wiring: rpc half in the post (distance)",
              p.get("distance_driven"), 48210)
        check("wiring: key consistent across halves",
              (p.get("challenge_id"), p.get("event_index"),
               p.get("stage_index")), (910001, 0, 2))
        check("wiring: full condition id passes through the snapshot half",
              p.get("stage_conditions_id"), 26)
    srv.shutdown()


def test_posttime_splits_e2e():
    """Official/TT path: the PostTime that precedes the StageComplete claims
    the UDP run and stashes its splits per vehicle; _record_proxy_stats hands
    them to the merge feed so the stage_runs row carries them."""
    import json as _json
    import types
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from dr2server import httpd
    from dr2server.egonet import Int64, UInt32

    posts = []

    class _Cap(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(n)
            if self.path == '/api/game/stage-detail':
                posts.append(_json.loads(body))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass

    srv = HTTPServer(('127.0.0.1', 0), _Cap)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    scratch = Path(r"D:\CODE\DR2\build\stage_detail_scratch")
    app = httpd.App(
        data_root=scratch, capture_root=scratch / "captures",
        upstream_ip="159.153.126.42", proxy_default_except_clubs=True,
        api_url=f"http://127.0.0.1:{port}", api_token="df_test")

    class _Run:
        track_length_m = 48210.0

        def segment_times(self, n):
            return [30.0] * (n or 0)

        def has_wrap(self):
            return False

        def max_distance(self):
            return 0.0

    class _Tel:
        def __init__(self):
            self.run = _Run()

        def take_run_since(self, wall):
            return self.run

    app.dispatcher.telemetry = _Tel()

    handler = httpd.RequestHandler.__new__(httpd.RequestHandler)
    handler.server = types.SimpleNamespace(app=app)

    # StageEnded snapshot half first (like the wire).
    dm_payload = {"Statistics": {"Data": {
        "event": "StageEnded",
        "keys": ["challenge_id", "event_index", "stage_index", "challenge_type",
                 "track_id", "location_id", "vehicle_class_id", "vehicle_id",
                 "player_finished", "race_position", "stage_time_seconds"],
        "values": [Int64(920001), UInt32(0), UInt32(2), Int64(1),
                   UInt32(636), UInt32(4), UInt32(97), UInt32(565),
                   True, UInt32(1), 155.99],
    }}}
    handler._log_datamining_event(dm_payload)

    # Official StageBegin arms the clock; PostTime claims + stashes the run.
    app.dispatcher.note_official_stage_begin(
        {"ChallengeId": Int64(920001), "EventIndex": UInt32(0),
         "StageIndex": UInt32(2), "VehicleId": UInt32(565),
         "LiveryId": UInt32(3813)})
    app.dispatcher._post_time({
        "TrackModelId": UInt32(636), "VehicleClassId": UInt32(97),
        "ConditionsId": UInt32(26), "Category": UInt32(1),
        "StageTime": 155.99, "VehicleId": UInt32(565), "LiveryId": UInt32(3813),
        "GhostData": b"\x01\x02ghost"})

    # StageComplete follows -> merge feed gets the stashed splits.
    rpc_payload = {
        "ChallengeId": Int64(920001), "EventIndex": UInt32(0),
        "StageIndex": UInt32(2),
        "VehicleId": UInt32(565), "LiveryId": UInt32(3813),
        "StageTime": 155.99, "RaceStatus": UInt32(0), "RecovToService": False,
        "MetersDriven": Int64(48210), "DistanceDriven": Int64(48210),
        "UsingWheel": True, "UsingAssists": False,
    }
    handler._record_proxy_stats("RaceNetChallenges.StageComplete", rpc_payload)

    deadline = time.time() + 5.0
    while not posts and time.time() < deadline:
        time.sleep(0.05)
    check("posttime-splits: exactly one merged POST", len(posts), 1)
    if posts:
        p = posts[0]
        splits = p.get("splits")
        check("posttime-splits: splits handed to the merge feed",
              None if splits is None else len(splits), 4)
        check("posttime-splits: splits scaled to the stage time",
              None if splits is None else round(sum(splits), 3), 155.99)
    srv.shutdown()


def main():
    test_rpc_first()
    test_dm_first()
    test_timeout_flush_dm_only()
    test_timeout_flush_rpc_only()
    test_dnf_shape()
    test_no_cross_contamination()
    test_duplicate_half()
    test_emit_failure_isolated()
    test_club_path_flat_index()
    test_httpd_wiring_e2e()
    test_posttime_splits_e2e()
    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
