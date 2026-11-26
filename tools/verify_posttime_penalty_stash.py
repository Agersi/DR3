r"""Verify the PostTime penalty detection + stash path (client v0.1.87).

Official/TT runs get no penalty decomposition anywhere else, so the client's
PostTime handler claims the UDP run (take_runs_since — one per lap on a
multi-lap circuit), detects the penalties the game baked into the reported
StageTime (in-run clock jumps + report-time excess, 4s floor) and stashes the
total per vehicle for the stage-detail merge that follows:

- official rows pick it up in httpd._record_proxy_stats (rpc_fields);
- TT rows pick it up in httpd._fix_stage_detail_penalty at emit time.

No capture needed: feeds fabricated 50 Hz samples through the live
TelemetryListener._on_sample code and drives the live dispatcher/httpd
methods. Checks:

  1. clean official rally pass -> nothing stashed, ring drained;
  2. in-run 8s car-reset jump -> ~8000 stashed (single take only);
  3. service over-time report excess -> counted; sub-4s drift -> not stashed;
  4. multi-lap RX: clean sum -> nothing, one lap's jump -> ~8000 via the
     _stage_penalty_ms_multi branch;
  5. club stage open on the track -> claim skipped, run left for the club
     StageComplete path;
  6. stale stash entry (past TTL) is not handed over;
  7. httpd._fix_stage_detail_penalty fills a TT record from the stash, an
     existing value wins without consuming the stash, missing vehicle_id is
     a no-op;
  8. e2e: a datamining-only TT record flushes on the buffer timeout with
     penalties_ms riding in the POST body.

Run: python tools/verify_posttime_penalty_stash.py   (from D:\CODE\DR2)
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

FAILS = 0

_DT = 0.02  # 50 Hz
_DWELL_S = 1.0  # start-box dwell before the drive
_NUM_FLOATS = 66


def check(label, cond, detail=""):
    global FAILS
    print(f"  {'PASS' if cond else 'FAIL'} {label}" + (f" — {detail}"
                                                       if detail else ""))
    if not cond:
        FAILS += 1


def approx_check(label, got, lo, hi):
    check(label, got is not None and lo <= got <= hi, f"{got}ms vs [{lo},{hi}]")


class _Cap(BaseHTTPRequestHandler):
    def __init__(self, *a, posts=None, **kw):
        self.posts = posts
        super().__init__(*a, **kw)

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(n)
        if self.path == '/api/game/stage-detail':
            self.posts.append(json.loads(body))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *a):
        pass


def make_app():
    from dr2server import httpd
    posts = []
    srv = HTTPServer(('127.0.0.1', 0),
                     lambda *a, **kw: _Cap(*a, posts=posts, **kw))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    scratch = Path(r"D:\CODE\DR2\build\posttime_penalty_stash_scratch")
    app = httpd.App(
        data_root=scratch, capture_root=scratch / "captures",
        upstream_ip="159.153.126.42", proxy_default_except_clubs=True,
        api_url=f"http://127.0.0.1:{port}", api_token="df_test")
    return app, posts, srv


def fresh_telemetry(app):
    from dr2server.telemetry import TelemetryListener
    listener = TelemetryListener(port=0)
    app.dispatcher.telemetry = listener
    app.dispatcher._telemetry_arm_wall = -5.0  # armed before the stage began
    return listener


def make_sample(clock: float, dist: float, speed: float, length: float):
    from dr2server import telemetry as t
    vals = [0.0] * _NUM_FLOATS
    vals[t.F_STAGE_TIME] = clock
    vals[t.F_DIST] = dist
    vals[t.F_SPEED] = speed
    vals[t.F_TRACK_LEN] = length
    return tuple(vals)


def feed_rally_pass(listener, stage_s: float, length: float,
                    jump_ms: float = 0.0) -> None:
    """One monotonic start->finish rally pass; optionally the game advances
    the stage clock instantly mid-run (a car reset)."""
    t = 0.0
    for _ in range(int(_DWELL_S / _DT)):
        listener._on_sample(t, make_sample(0.0, 2.0, 0.0, length))
        t += _DT
    n_drive = max(2, int(stage_s / _DT))
    jumped = 0.0
    for k in range(1, n_drive + 1):
        frac = k / n_drive
        if jump_ms and k == n_drive // 2:
            jumped = jump_ms / 1000.0
        listener._on_sample(t, make_sample(stage_s * frac + jumped,
                                           2.0 + (length - 2.0) * frac,
                                           (length - 2.0) / stage_s, length))
        t += _DT


def feed_rx_laps(listener, laps_s: list, length: float, jump_lap: int = -1,
                 jump_ms: float = 8000.0) -> None:
    """One dwell+drive per lap; wall time is continuous, the stage clock
    resets each lap (a multi-lap rallycross circuit)."""
    t = 0.0
    for li, lap_time in enumerate(laps_s):
        for _ in range(int(_DWELL_S / _DT)):
            listener._on_sample(t, make_sample(0.0, 2.0, 0.0, length))
            t += _DT
        n_drive = max(2, int(lap_time / _DT))
        jumped = 0.0
        for k in range(1, n_drive + 1):
            frac = k / n_drive
            if jump_lap == li and k == n_drive // 2:
                jumped = jump_ms / 1000.0
            listener._on_sample(t, make_sample(lap_time * frac + jumped,
                                               2.0 + (length - 2.0) * frac,
                                               (length - 2.0) / lap_time,
                                               length))
            t += _DT


def post_time(app, stage_s: float):
    d = app.dispatcher
    return d._post_time({
        "VehicleId": 565, "LiveryId": 0, "TrackModelId": 580,
        "NationalityId": 0, "ConditionsId": 1, "Category": 0,
        "UsingWheel": False, "UsingAssists": False,
        "StageTime": stage_s, "GhostData": b"",
    })


def main() -> int:
    app, posts, srv = make_app()
    d = app.dispatcher
    real_api_client = d.api_client
    d.api_client = None  # keep the _post_time cases off any submission path
    try:
        # -- case 1: clean official rally pass ------------------------------
        print("case 1: clean official rally pass")
        listener = fresh_telemetry(app)
        feed_rally_pass(listener, 300.0, 25_000.0)
        post_time(app, 300.0)
        check("clean run -> nothing stashed",
              d.take_posttime_penalty(565) is None)
        check("run claimed (ring drained)", listener.take_runs_since(-5.0) == [])

        # -- case 2: in-run car reset ----------------------------------------
        print("case 2: in-run 8s car reset")
        listener = fresh_telemetry(app)
        feed_rally_pass(listener, 300.0, 25_000.0, jump_ms=8000.0)
        post_time(app, 308.0)  # the game bakes the jump into StageTime
        approx_check("8s jump detected (~8000ms)", d.take_posttime_penalty(565),
                     7400, 8600)
        check("second take is empty", d.take_posttime_penalty(565) is None)

        # -- case 3: report-time excess ---------------------------------------
        print("case 3: service over-time")
        listener = fresh_telemetry(app)
        feed_rally_pass(listener, 300.0, 25_000.0)
        post_time(app, 310.0)
        approx_check("10s report excess counted", d.take_posttime_penalty(565),
                     9400, 10600)
        listener = fresh_telemetry(app)
        feed_rally_pass(listener, 300.0, 25_000.0)
        post_time(app, 302.0)
        check("sub-4s drift floored to nothing",
              d.take_posttime_penalty(565) is None)

        # -- case 4: multi-lap RX ---------------------------------------------
        print("case 4: multi-lap rallycross")
        laps_s = [57.2, 65.8, 48.7]
        listener = fresh_telemetry(app)
        feed_rx_laps(listener, laps_s, 2000.0)
        post_time(app, sum(laps_s))
        check("clean lap set -> nothing stashed",
              d.take_posttime_penalty(565) is None)
        listener = fresh_telemetry(app)
        feed_rx_laps(listener, laps_s, 2000.0, jump_lap=1, jump_ms=8000.0)
        post_time(app, sum(laps_s) + 8.0)
        approx_check("one lap's 8s jump detected (~8000ms)",
                     d.take_posttime_penalty(565), 7400, 8600)

        # -- case 5: club stage open on the track -----------------------------
        print("case 5: club stage owns the run")
        listener = fresh_telemetry(app)
        feed_rally_pass(listener, 300.0, 25_000.0, jump_ms=8000.0)
        d._open_club_stages["evt-test"] = 580
        try:
            post_time(app, 308.0)
            check("club run -> nothing stashed",
                  d.take_posttime_penalty(565) is None)
            left = listener.take_runs_since(-5.0)
            check("run left unclaimed for the club StageComplete path",
                  len(left) == 1, f"{len(left)} left")
        finally:
            del d._open_club_stages["evt-test"]

        # -- case 6: TTL -------------------------------------------------------
        print("case 6: stale stash entry")
        d._stash_posttime_penalty(565, 8000)
        d._pending_posttime_penalty[565] = (time.time() - 121.0, 8000)
        check("stale entry is not handed over",
              d.take_posttime_penalty(565) is None)

        # -- case 7: httpd._fix_stage_detail_penalty ---------------------------
        print("case 7: TT emit-time fill")
        d._stash_posttime_penalty(565, 8000)
        rec = {"vehicle_id": 565}
        app._fix_stage_detail_penalty(rec)
        check("missing penalties_ms filled from the stash",
              rec.get("penalties_ms") == 8000, f'rec={rec}')
        check("stash consumed by the fill",
              d.take_posttime_penalty(565) is None)
        d._stash_posttime_penalty(565, 4000)
        rec2 = {"vehicle_id": 565, "penalties_ms": 9000}
        app._fix_stage_detail_penalty(rec2)
        check("existing value wins, untouched",
              rec2.get("penalties_ms") == 9000, f'rec={rec2}')
        check("stash NOT consumed when the record already had one",
              d.take_posttime_penalty(565) == 4000)
        rec3 = {}
        app._fix_stage_detail_penalty(rec3)
        check("missing vehicle_id is a no-op", "penalties_ms" not in rec3,
              f'rec={rec3}')

        # -- case 8: e2e TT flush ----------------------------------------------
        print("case 8: datamining-only TT record flushes with the penalty")
        d.api_client = real_api_client  # _emit_stage_detail needs it to post
        d._stash_posttime_penalty(565, 8000)
        buf = app.stage_detail
        old_timeout = buf.FLUSH_TIMEOUT_S
        old_tt_timeout = buf.TT_FLUSH_TIMEOUT_S
        buf.FLUSH_TIMEOUT_S = 0.3
        buf.TT_FLUSH_TIMEOUT_S = 0.3
        try:
            buf.feed_datamining({
                "challenge_id": 0, "event_index": 0, "stage_index": 0,
                "challenge_type": -1, "track_model_id": 580, "vehicle_id": 565,
                "player_finished": True, "player_end_status": 0,
                "race_position": 1, "stage_time_seconds": 308.0,
                "stage_conditions_id": 1, "metres_cam_head": 25_000.0,
                "g_session_id": "sidP",
            })
        finally:
            buf.FLUSH_TIMEOUT_S = old_timeout
            buf.TT_FLUSH_TIMEOUT_S = old_tt_timeout
        n0 = len(posts)
        deadline = time.time() + 5.0
        while len(posts) == n0 and time.time() < deadline:
            time.sleep(0.05)
        check("TT record flushed", len(posts) == n0 + 1, f"{len(posts)} posts")
        if len(posts) > n0:
            p = posts[-1]
            check("penalties_ms rides in the POST body",
                  p.get("penalties_ms") == 8000, f'body={p}')
    finally:
        srv.shutdown()

    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
