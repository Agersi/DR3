"""Functional checks for the httpd lockdown (Phase 2 C4/C5).

Starts a real App + listener on an ephemeral port and verifies:
  - Host allow-list (403 for foreign hosts, 200 for Codemasters domains)
  - removed routes are gone (/register, /login -> 404)
  - POST without X-EgoNet-Function is rejected (403)
  - oversized Content-Length is rejected (413) without reading the body
  - captures never persist X-EgoNet-SessionID
  - SKIP_CAPTURE_METHODS still suppress capture files

Usage: python tools/test_httpd_lockdown.py [port]   (default: ephemeral)
"""
from __future__ import annotations

import http.client
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dr3"))

from dr2server.httpd import App, create_server  # noqa: E402
from dr2server.egonet import encode_stream  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    workdir = Path(tempfile.mkdtemp(prefix="dr3_httpd_lockdown_", dir=ROOT / "build"))
    app = App(
        data_root=workdir / "data",
        capture_root=workdir / "captures",
        capturing=True,
        app_version="lockdown-test",
    )
    server = create_server("127.0.0.1", port, app)
    real_port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _run_checks(real_port, workdir / "captures")
    finally:
        server.shutdown()
        server.server_close()

    # C5: honest failure when the upstream is unreachable for result-storing
    # methods. Upstream 127.0.0.1:443 refuses immediately (nothing listens).
    proxy_app = App(
        data_root=workdir / "proxy_data",
        capture_root=workdir / "proxy_captures",
        upstream_ip="127.0.0.1",
        proxy_default_except_clubs=True,
        capturing=False,
        app_version="lockdown-test",
    )
    proxy_server = create_server("127.0.0.1", 0, proxy_app)
    proxy_port = proxy_server.server_address[1]
    threading.Thread(target=proxy_server.serve_forever, daemon=True).start()
    try:
        _run_c5_checks(proxy_port)
    finally:
        proxy_server.shutdown()
        proxy_server.server_close()

    print(f"\n{PASS} passed, {FAIL} failed (workdir: {workdir})")
    return 1 if FAIL else 0


def _run_c5_checks(port: int) -> None:
    # StageComplete with a non-local (empty) challenge is proxied; the dead
    # upstream must yield an honest X-EgoNet-Result: 1, not a fake success.
    status, headers, body = _post_eenet(
        port, "RaceNetChallenges.StageComplete", encode_stream({}),
    )
    check("C5 StageComplete (dead upstream) -> 200 transport", status == 200, f"got {status}")
    result_hdr = headers.get("X-EgoNet-Result") or headers.get("x-egonet-result")
    check("C5 honest failure X-EgoNet-Result: 1", result_hdr == "1", f"got {result_hdr!r}")
    from dr2server.egonet import decode_stream
    try:
        decoded = decode_stream(body)
    except Exception:
        decoded = None
    check("C5 failure body is an empty egonet stream", decoded == {}, f"got {decoded!r}, raw={body[:16]!r}")

    # A read-only method with a dead upstream keeps its local fallback
    # (X-EgoNet-Result: 0) instead of failing honestly.
    status, headers, body = _post_eenet(
        port, "RaceNetChallenges.GetChallengeRecap", encode_stream({"ChallengeId": 12345}),
    )
    result_hdr = headers.get("X-EgoNet-Result") or headers.get("x-egonet-result")
    check("C5 read-only fallback X-EgoNet-Result: 0", status == 200 and result_hdr == "0",
          f"got status={status} result={result_hdr!r}")


def _get(port: int, host_header: str, path: str = "/"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"Host": host_header})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def _post_eenet(port: int, function: str, body: bytes, extra_headers=None):
    headers = {
        "Host": "prod.egonet.codemasters.com",
        "X-EgoNet-Function": function,
        "Content-Type": "application/egonet-stream",
    }
    if extra_headers:
        headers.update(extra_headers)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("POST", "/RP17/1.18.0/STEAM/", body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, dict(resp.getheaders()), data


def _run_checks(port: int, capture_root: Path) -> None:
    # 1. Host allow-list on GET
    status, body = _get(port, "prod.egonet.codemasters.com")
    check("GET / with CM host -> 200", status == 200, f"got {status}")
    check("GET / serves DR3 home page", b"DR3 Local Server" in body)

    status, _ = _get(port, "evil.example.com")
    check("GET / with foreign host -> 403", status == 403, f"got {status}")

    status, _ = _get(port, "localhost")
    check("GET / with localhost host -> 200", status == 200, f"got {status}")

    # 2. Removed routes are gone
    status, _ = _get(port, "prod.egonet.codemasters.com", "/register")
    check("GET /register -> 404 (route removed)", status == 404, f"got {status}")
    status, _ = _get(port, "prod.egonet.codemasters.com", "/login")
    check("GET /login -> 404 (route removed)", status == 404, f"got {status}")

    # 3. POST without X-EgoNet-Function is rejected
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST", "/api/account/register", body=b'{"username":"x"}',
        headers={"Host": "prod.egonet.codemasters.com", "Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    resp.read()
    conn.close()
    check("POST without X-EgoNet-Function -> 403", resp.status == 403, f"got {resp.status}")

    # 4. Oversized Content-Length rejected without reading the body (raw socket:
    #    declare a huge length, send no body at all)
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    req = (
        b"POST /RP17/1.18.0/STEAM/ HTTP/1.1\r\n"
        b"Host: prod.egonet.codemasters.com\r\n"
        b"X-EgoNet-Function: RaceNetChallenges.StageComplete\r\n"
        b"Content-Length: 99999999\r\n"
        b"\r\n"
    )
    sock.sendall(req)
    data = b""
    deadline = time.time() + 10
    while b"\r\n\r\n" not in data and time.time() < deadline:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    sock.close()
    head = data.split(b"\r\n\r\n", 1)[0].decode("ascii", errors="replace")
    check("oversized Content-Length -> 413", " 413 " in head.split("\r\n")[0], f"got {head.splitlines()[0] if head else 'no response'}")

    # 5. Legit local method still works + session ID never persisted
    before = set(capture_root.glob("*.json"))
    status, headers, data = _post_eenet(
        port, "RaceNet.CheckAccountLinked", encode_stream({}),
        extra_headers={"X-EgoNet-SessionID": "SECRET_SESSION_VALUE_123"},
    )
    check("CheckAccountLinked (always-local) -> 200", status == 200, f"got {status}")
    time.sleep(0.3)
    new_files = set(capture_root.glob("*.json")) - before
    leaked = False
    for f in new_files:
        if "SECRET_SESSION_VALUE_123" in f.read_text(encoding="utf-8"):
            leaked = True
    check("session ID stripped from captures", not leaked)

    # 6. SKIP_CAPTURE_METHODS still suppresses capture files
    before = set(capture_root.glob("*.json"))
    status, _, _ = _post_eenet(
        port, "Login.Login", encode_stream({"Account": {"Name": "probe"}}),
        extra_headers={"X-EgoNet-SessionID": "SECRET_SESSION_VALUE_456"},
    )
    check("Login.Login (skip-capture) -> 200", status == 200, f"got {status}")
    time.sleep(0.3)
    new_files = set(capture_root.glob("*.json")) - before
    check("Login.Login wrote no capture file", not any("Login_Login" in f.name for f in new_files),
          f"files: {[f.name for f in new_files]}")


if __name__ == "__main__":
    sys.exit(main())
