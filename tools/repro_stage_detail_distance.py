#!/usr/bin/env python3
"""Verify the stage-detail distance + camera-counter correction (client v0.1.70).

The game's StageEnded snapshot carries no per-stage distance and its
metres_cam_* fields are CUMULATIVE counters: they accumulate across repeats of
the same time trial (same session + track + conditions) and reset whenever the
stage is reloaded. Time trials send no StageComplete RPC, so their stage_runs
rows previously stored the cumulative camera sum as distance_m.

The fix corrects both at emit time (httpd.App._emit_stage_detail), anchored to
the run's real distance D (measured from telemetry for TTs, RPC value for
club/official):

- raw sum ~= D                    -> fresh counter, keep the raw values;
- else previous StageEnded on the same (session, track, conditions) epoch and
  its delta sum ~= D              -> per-camera deltas;
- otherwise                       -> scale the raw values proportionally to D.

Run: python tools/repro_stage_detail_distance.py   (from D:\\CODE\\DR2)
"""
import json
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, r"D:\CODE\DR2\dr3")

FAILS = 0


def check(label, got, want):
    global FAILS
    ok = got == want
    print(f"[{'OK' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS += 1


def approx_check(label, got, want, tol=1.0):
    global FAILS
    ok = got is not None and abs(got - want) <= tol
    print(f"[{'OK' if ok else 'FAIL'}] {label}: got={got!r} want~={want!r}")
    if not ok:
        FAILS += 1


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
    scratch = Path(r"D:\CODE\DR2\build\stage_detail_distance_scratch")
    app = httpd.App(
        data_root=scratch, capture_root=scratch / "captures",
        upstream_ip="159.153.126.42", proxy_default_except_clubs=True,
        api_url=f"http://127.0.0.1:{port}", api_token="df_test")
    return app, posts, srv


def tt_record(sid, track, conditions, head, *, dnf=False, distance=None):
    """A datamining-only TT record as the buffer would merge it."""
    rec = {
        "challenge_id": 0, "event_index": 0, "stage_index": 0,
        "challenge_type": -1, "track_model_id": track, "vehicle_id": 565,
        "player_finished": not dnf, "player_end_status": 5 if dnf else 0,
        "race_position": 0 if dnf else 1,
        "stage_time_seconds": 140.0,
        "stage_conditions_id": conditions,
        "metres_cam_bonnet": 0.0, "metres_cam_bumper": 0.0,
        "metres_cam_chase_close": 0.0, "metres_cam_chase_far": 0.0,
        "metres_cam_dash": 0.0, "metres_cam_head": float(head),
        "g_session_id": sid,
    }
    if dnf:
        rec["race_status"] = 5
    if distance is not None:
        rec["distance_driven"] = distance
    return rec


def emit(app, rec):
    app._fix_stage_detail_distance(rec)
    app._fix_stage_detail_cameras(rec)
    return rec


def test_distance_enrichment(app):
    d = app.dispatcher
    d._stash_posttime_distance(565, 4550)
    rec = emit(app, tt_record("sidA", 580, 1, 4550))
    check("distance: missing distance_driven filled from the stash",
          rec.get("distance_driven"), 4550)
    # The stash entry is consumed by the first take.
    rec2 = emit(app, tt_record("sidA", 580, 1, 4550))
    check("distance: stash popped (second record not re-filled)",
          rec2.get("distance_driven"), None)

    d._stash_posttime_distance(565, 999)
    rec3 = emit(app, tt_record("sidA", 580, 1, 4550, distance=1234))
    check("distance: existing RPC value wins, untouched",
          rec3.get("distance_driven"), 1234)
    check("distance: stash NOT consumed when the record already had one",
          d.take_posttime_distance(565), 999)


def test_cam_fresh_counter(app):
    # First attempt on a fresh TT screen: raw sum == D -> keep the raw values.
    rec = emit(app, tt_record("sidB", 565, 1, 2842, distance=2842))
    check("cam-fresh: raw values kept when they match D",
          rec.get("metres_cam_head"), 2842.0)


def test_cam_accumulated_repeats(app):
    # The 580 series: same session + track + conditions, counter accumulates.
    r1 = emit(app, tt_record("sidC", 580, 1, 4550, distance=4550))
    check("cam-acc: attempt 1 fresh -> raw kept",
          r1.get("metres_cam_head"), 4550.0)

    # Attempt 2: game counter now reads 9100 (cumulative), real run = 4560.
    r2 = emit(app, tt_record("sidC", 580, 1, 9100, distance=4560))
    check("cam-acc: attempt 2 -> per-camera delta stored",
          r2.get("metres_cam_head"), 4550.0)

    # Attempt 3: counter 13660, real run = 4555.
    r3 = emit(app, tt_record("sidC", 580, 1, 13660, distance=4555))
    approx_check("cam-acc: attempt 3 -> delta vs the PREVIOUS raw value",
                 r3.get("metres_cam_head"), 4560.0)


def test_cam_conditions_change_resets(app):
    # The 565 pair: dry (conditions=1) then wet (conditions=11) — reloading
    # with new conditions resets the game counter, so the second attempt's raw
    # value is already per-stage even though a baseline exists for the track.
    r1 = emit(app, tt_record("sidD", 565, 1, 2842, distance=2842))
    check("cam-cond: dry attempt fresh -> raw kept",
          r1.get("metres_cam_head"), 2842.0)

    r2 = emit(app, tt_record("sidD", 565, 11, 2814, distance=2814))
    check("cam-cond: wet attempt uses its OWN epoch (no stale baseline)",
          r2.get("metres_cam_head"), 2814.0)


def test_cam_no_anchor_keeps_raw(app):
    # Telemetry off + no RPC: no D available -> the raw values stay as-is
    # (today's behavior; better to store the inflated value than invent one).
    rec = emit(app, tt_record("sidE", 580, 1, 27354))
    check("cam-noD: without a distance anchor the raw values are kept",
          rec.get("metres_cam_head"), 27354.0)


def test_cam_scale_fallback(app):
    # Inflated counter with NO usable baseline (e.g. the client restarted mid
    # sequence and lost it): scale proportionally to D. Single camera -> exact.
    rec = emit(app, tt_record("sidF", 580, 1, 27354, distance=4608))
    approx_check("cam-scale: single camera scaled to D",
                 rec.get("metres_cam_head"), 4608.0)

    # Multi-camera split is preserved proportionally (sum lands on D).
    rec2 = tt_record("sidF", 580, 1, 17354, distance=4608)
    rec2["metres_cam_bonnet"] = 10000.0
    emit(app, rec2)
    total = (rec2.get("metres_cam_head", 0.0)
             + rec2.get("metres_cam_bonnet", 0.0))
    approx_check("cam-scale: multi-camera sum lands on D", total, 4608.0)
    check("cam-scale: zero cameras stay zero",
          rec2.get("metres_cam_dash"), 0.0)


def test_cam_dnf_updates_baseline_only(app):
    # A DNF advances the counters but its own values are untrusted (the web
    # stores NULL distance for it): keep raw, still refresh the baseline so the
    # next clean attempt's delta is measured against it.
    r1 = emit(app, tt_record("sidG", 580, 1, 4550, distance=4550))
    check("cam-dnf: attempt 1 fresh -> raw kept",
          r1.get("metres_cam_head"), 4550.0)

    dnf = emit(app, tt_record("sidG", 580, 1, 9100, distance=4550, dnf=True))
    check("cam-dnf: DNF row keeps its raw values",
          dnf.get("metres_cam_head"), 9100.0)

    r2 = emit(app, tt_record("sidG", 580, 1, 13660, distance=4560))
    approx_check("cam-dnf: next clean attempt deltas against the DNF raw value",
                 r2.get("metres_cam_head"), 4560.0)


def test_tt_timeout_flush_e2e(app, posts):
    # Full wiring: a datamining-only TT record (no StageComplete RPC ever
    # arrives) flushes on the buffer timeout through _emit_stage_detail with
    # both corrections applied, and g_session_id rides along from the snapshot.
    from dr2server import stage_detail as sd
    check("e2e: g_session_id is a kept StageEnded field",
          "g_session_id" in sd.STAGE_ENDED_FIELDS, True)

    app.dispatcher._stash_posttime_distance(565, 4550)
    buf = app.stage_detail
    old_timeout = buf.FLUSH_TIMEOUT_S
    buf.FLUSH_TIMEOUT_S = 0.3
    try:
        dm = {
            "challenge_id": 0, "event_index": 0, "stage_index": 0,
            "challenge_type": -1, "track_model_id": 580, "vehicle_id": 565,
            "player_finished": True, "player_end_status": 0,
            "stage_time_seconds": 140.0, "stage_conditions_id": 1,
            "metres_cam_head": 9100.0,
            "g_session_id": "sidH",
        }
        # Pre-seed the epoch baseline like a previous attempt would have.
        with app._cam_lock:
            app._cam_prev[("sidH", "580", "1")] = {k: 4550.0 for k in app._CAM_FIELDS}
        buf.feed_datamining(dm)
    finally:
        buf.FLUSH_TIMEOUT_S = old_timeout

    deadline = time.time() + 5.0
    while not posts and time.time() < deadline:
        time.sleep(0.05)
    check("e2e: TT record flushed via the wrapped send callback", len(posts), 1)
    if posts:
        p = posts[0]
        check("e2e: distance filled from the stashed run distance",
              p.get("distance_driven"), 4550)
        approx_check("e2e: cumulative camera value corrected to the delta",
                     p.get("metres_cam_head"), 4550.0)


def main():
    app, posts, srv = make_app()
    try:
        test_distance_enrichment(app)
        test_cam_fresh_counter(app)
        test_cam_accumulated_repeats(app)
        test_cam_conditions_change_resets(app)
        test_cam_no_anchor_keeps_raw(app)
        test_cam_scale_fallback(app)
        test_cam_dnf_updates_baseline_only(app)
        test_tt_timeout_flush_e2e(app, posts)
    finally:
        srv.shutdown()
    print()
    if FAILS:
        print(f"FAILED: {FAILS} check(s)")
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
