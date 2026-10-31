"""Upstream stage-split probe: run DR2 against the REAL EGO net through a
local transparent proxy and capture every request/response pair locally.

Why: our _stage_splits answer is an empty stub ({"Splits": [], "Entries": []})
and no non-empty upstream response has ever been captured, so the wire format
of Splits/Entries is unknown. This tool proxies 100% of the game traffic to
the real EgoNet (no DR3 clubs at all), so an official upstream club challenge
can be entered and driven; every RPC — including GetStageSplits with real
data — is captured to build/upstream_split_captures/.

Capture completeness: EVERY HTTP request that reaches the server is written
to a capture file BEFORE routing (do_GET/do_POST plus a catch-all for any
other method), and every proxied response is appended to that same file
(decoded body + raw base64 + headers + status). Unknown GET paths and
non-POST methods are forwarded upstream transparently instead of 404/501,
and an unexpected proxy failure is recorded in the capture file. All
DataMining.DataEvent types (StageStarted/Ended, EventStarted/Ended,
ChampionshipStarted/Ended, DriveStarted, ReplayStateExited, ...) arrive as
plain proxied POSTs and are captured like everything else; each one also
prints a [DATA] line to the console live.

Usage (repo root, Windows):
  python tools/probe_upstream_splits.py             # start the proxy server
  python tools/probe_upstream_splits.py --report    # analyze captured traffic
  python tools/probe_upstream_splits.py --restore   # remove hosts entries and exit

Notes:
- Reuses the DR3 client's existing hosts redirect + trusted dev cert (the
  ones installed by Easy Setup), so normally no elevation is needed. If the
  hosts redirect is missing, run from an elevated terminal.
- Stop the DR3 client first (it owns port 443).
- In-game: log in as usual, open Clubs, enter the last/official club and
  drive stages — GetStageSplits fires each time the game prepares a stage
  (right after every StageComplete of the previous one, or on resume).
"""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import socket
import ssl
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dr3"))

import dr3_app  # noqa: E402  (paths/cert/hosts helpers; no GUI on import)
from dr2server import httpd  # noqa: E402
from dr2server.egonet import decode_stream, encode_stream  # noqa: E402


class _AllSet(set):
    """Sentinel set: every method proxies upstream (httpd checks membership)."""

    def __contains__(self, item: object) -> bool:
        return True


def u(v: Any) -> Any:
    """Unwrap egonet scalar wrappers to plain values."""
    return getattr(v, "value", v)


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


class ProbeRequestHandler(httpd.RequestHandler):
    """The stock handler plus total transparency.

    Stock behavior already captures every GET and POST before routing and
    appends the upstream response to the capture file for proxied calls.
    Added here:
      - do_GET: unknown paths are proxied upstream instead of 404 (the local
        HTML/health pages are kept for manual checks).
      - any other HTTP method (PUT/DELETE/OPTIONS/HEAD/PATCH) is captured and
        forwarded upstream instead of the stock 501.
      - _handle_egonet_proxy: an unexpected crash in the proxy path is
        recorded in the capture file and a stub answer keeps the game alive
        (the stock graceful upstream-failure fallback already leaves the
        request-side capture intact).
    """

    _LOCAL_GET = {
        "/": httpd.HTML_HOME,
        "/register": httpd.HTML_REGISTER,
        "/login": httpd.HTML_LOGIN,
    }

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        capture_path = self.app.capture(
            {
                "method": self.command,
                "path": parsed.path,
                "query": parse_qs(parsed.query),
                "headers": dict(self.headers.items()),
                "body_text": "",
                "decoded_body": {},
            }
        )
        if parsed.path == "/api/health":
            return self._send_json(
                {"ok": True, "status": "healthy", "capture_file": str(capture_path)}
            )
        if parsed.path in self._LOCAL_GET:
            return self._send_html(self._LOCAL_GET[parsed.path])
        self._forward_generic("GET")

    def do_PUT(self) -> None:
        self._forward_generic("PUT")

    def do_DELETE(self) -> None:
        self._forward_generic("DELETE")

    def do_OPTIONS(self) -> None:
        self._forward_generic("OPTIONS")

    def do_HEAD(self) -> None:
        self._forward_generic("HEAD")

    def do_PATCH(self) -> None:
        self._forward_generic("PATCH")

    def _handle_egonet_proxy(self, method: str, capture_path: Path) -> None:
        try:
            return super()._handle_egonet_proxy(method, capture_path)
        except Exception as exc:
            self.app.capture_response(
                capture_path, {"proxy_error": repr(exc), "fallback": "local stub sent"}
            )
            print(f"[PROXY] UNHANDLED ERROR in {method}: {exc!r} — sending stub to keep the game alive")
            body = encode_stream({"Accepted": True})
            self._send_bytes(
                body,
                content_type="application/egonet-stream",
                extra_headers={"X-EgoNet-Result": "0"},
            )

    def _forward_generic(self, method: str) -> None:
        """Capture + transparently forward any non-POST (or unknown-path GET) request."""
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b""
        capture_path = self.app.capture(
            {
                "method": method,
                "path": parsed.path,
                "query": parse_qs(parsed.query),
                "headers": dict(self.headers.items()),
                "body_text": raw_body.decode("utf-8", errors="replace"),
                "body_base64": base64.b64encode(raw_body).decode("ascii"),
                "decoded_body": self._decode_body(raw_body) if raw_body else {},
            }
        )
        host = self.headers.get("Host", "prod.egonet.codemasters.com")
        upstream_headers: Dict[str, str] = {}
        for key, value in self.headers.items():
            kl = key.lower()
            if kl.startswith("x-egonet-") or kl in ("content-type", "user-agent"):
                upstream_headers[key] = value
        if self.app.upstream_session and any(
            k.lower() == "x-egonet-sessionid" for k in upstream_headers
        ):
            upstream_headers["X-EgoNet-SessionID"] = self.app.upstream_session
        upstream_headers["Host"] = host

        print(f"[PROXY] {method} {parsed.path} -> {self.app.upstream_ip}:443 (host={host})")
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            class _Conn(http.client.HTTPSConnection):
                def connect(self) -> None:  # SNI must match the Host header
                    sock = socket.create_connection(
                        (self.host, self.port), self.timeout, self.source_address
                    )
                    self.sock = self._context.wrap_socket(sock, server_hostname=host)

            conn = _Conn(self.app.upstream_ip, 443, timeout=15, context=ctx)
            conn.request(method, parsed.path, body=raw_body or None, headers=upstream_headers)
            resp = conn.getresponse()
            resp_body = resp.read()
            resp_headers = {key: value for key, value in resp.getheaders()}
            conn.close()
        except Exception as exc:
            print(f"[PROXY] ERROR forwarding {method} {parsed.path}: {exc}")
            self.app.capture_response(
                capture_path, {"proxy_error": repr(exc), "fallback": "local stub sent"}
            )
            body = encode_stream({"Accepted": True})
            self._send_bytes(
                body,
                content_type="application/egonet-stream",
                extra_headers={"X-EgoNet-Result": "0"},
            )
            return

        upstream_result = resp_headers.get("X-EgoNet-Result", resp_headers.get("x-egonet-result", "?"))
        print(f"[PROXY] {method} {parsed.path} upstream returned: status={resp.status}, X-EgoNet-Result={upstream_result}, body_len={len(resp_body)}")

        if "X-EgoNet-SessionID" in resp_headers or "x-egonet-sessionid" in resp_headers:
            sid = resp_headers.get("X-EgoNet-SessionID") or resp_headers.get("x-egonet-sessionid")
            self.app.upstream_session = sid

        decoded_upstream: Dict[str, Any] = {}
        if resp_body:
            try:
                decoded_upstream = decode_stream(resp_body)
            except Exception:
                decoded_upstream = {"raw_base64": base64.b64encode(resp_body).decode("ascii")}

        self.app.capture_response(
            capture_path,
            {
                "proxy": True,
                "upstream_status": resp.status,
                "headers": dict(resp_headers),
                "body_raw_base64": base64.b64encode(resp_body).decode("ascii"),
                "body_decoded": decoded_upstream,
            },
        )

        extra_headers: Dict[str, str] = {}
        for key, value in resp_headers.items():
            kl = key.lower()
            if kl.startswith("x-egonet-") or kl == "content-type":
                extra_headers[key] = value
        self.send_response(httpd.HTTPStatus(resp.status) if 100 <= resp.status <= 599 else httpd.HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            resp_headers.get("Content-Type", resp_headers.get("content-type", "application/egonet-stream")),
        )
        self.send_header("Content-Length", str(len(resp_body)))
        for key, value in extra_headers.items():
            if key.lower() not in ("content-type", "content-length"):
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(resp_body)


def setup(upstream_ip: str) -> None:
    """Verify hosts redirect + trusted cert (reuses the DR3 client's)."""
    if not dr3_app.cert_exists():
        print("No DR3 cert found — generating one (same location the client uses).")
        dr3_app.generate_cert()
    if not dr3_app.hosts_configured():
        if not dr3_app.is_admin():
            print("hosts redirect is not configured and this process is not elevated.")
            print("Re-run from an elevated terminal, or run the DR3 client's Easy Setup once.")
            sys.exit(1)
        dr3_app.add_hosts()
        if not dr3_app.install_cert_trust():
            print("WARNING: could not install the cert into the machine Root store;")
            print("in-game TLS may fail. (The DR3 client's Easy Setup does this too.)")
    else:
        print("hosts redirect already configured (DR3 Easy Setup).")
    print(f"Upstream EGO net: {upstream_ip}:443")


def _summary(capture_dir: Path) -> None:
    files = sorted(capture_dir.glob("*.json"))
    by_method: Dict[str, int] = {}
    splits = 0
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        fn = (d.get("headers") or {}).get("X-EgoNet-Function") or d.get("path", "?")
        by_method[fn] = by_method.get(fn, 0) + 1
        if fn == "RaceNetChallenges.GetStageSplits":
            splits += 1
    print(f"\n=== Session summary: {len(files)} requests captured ===")
    for fn in sorted(by_method):
        marker = "  <-- SPLITS" if fn == "RaceNetChallenges.GetStageSplits" else ""
        print(f"  {by_method[fn]:4d}  {fn}{marker}")
    if splits:
        print("\nRun --report for the full GetStageSplits structure dump.")


def run_server(upstream_ip: str, capture_dir: Path) -> None:
    if port_in_use(443):
        print("Port 443 is busy — stop the DR3 client first (Stop button), then re-run.")
        sys.exit(1)
    capture_dir.mkdir(parents=True, exist_ok=True)
    data_dir = ROOT / "build" / "upstream_split_probe_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    app = httpd.App(
        data_root=data_dir,
        capture_root=capture_dir,
        upstream_ip=upstream_ip,
        proxy_methods=_AllSet(),
        capturing=True,
    )
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(certfile=str(dr3_app.CERT_PATH), keyfile=str(dr3_app.KEY_PATH))

    servers = []
    http_server = ThreadingHTTPServer(("127.0.0.1", 8080), ProbeRequestHandler)
    http_server.app = app  # type: ignore[attr-defined]
    servers.append(http_server)
    https_server = ThreadingHTTPServer(("127.0.0.1", 443), ProbeRequestHandler)
    https_server.app = app  # type: ignore[attr-defined]
    https_server.socket = ssl_context.wrap_socket(https_server.socket, server_side=True)
    servers.append(https_server)
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()

    print(f"Proxy server up — ALL traffic -> {upstream_ip}:443 (real EGO net, no DR3 clubs)")
    print(f"Captures: {capture_dir}")
    print("Every request is captured before routing; every upstream response is appended to it.")
    print("Now: launch DR2, log in, open Clubs, enter the last/official club and drive stages.")
    print("(GetStageSplits fires each time the game prepares a stage.)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for s in servers:
            s.shutdown()
            s.server_close()
        _summary(capture_dir)
        print("Stopped. Run with --report to analyze the captures.")


def describe_items(items: Any, label: str) -> None:
    if items is None:
        print(f"   {label}: <absent>")
        return
    if not isinstance(items, list):
        print(f"   {label}: {type(items).__name__} = {items!r}")
        return
    print(f"   {label}: list[{len(items)}]")
    if not items:
        return
    fields: Dict[str, str] = {}
    for it in items:
        if isinstance(it, dict):
            for k, v in it.items():
                fields.setdefault(k, type(v).__name__)
    print(f"     fields: {fields}")
    print(f"     sample[0]: {json.dumps(items[0], default=u)[:800]}")
    if len(items) > 1:
        print(f"     sample[1]: {json.dumps(items[1], default=u)[:800]}")


def dump_splits(d: Dict[str, Any]) -> None:
    resp = d.get("response") or {}
    body = resp.get("body_decoded") or resp.get("body") or {}
    if isinstance(body, str):
        print(f"   response: {body!r}")
        return
    hdrs = resp.get("headers") or {}
    print(
        f"   response: status={resp.get('upstream_status', '?')} "
        f"X-EgoNet-Result={hdrs.get('X-EgoNet-Result', hdrs.get('x-egonet-result', '?'))}"
    )
    for key in ("Splits", "Entries"):
        describe_items(body.get(key), key)
    extra = {k: v for k, v in body.items() if k not in ("Splits", "Entries")}
    if extra:
        print(f"   other top-level keys: {json.dumps(extra, default=u)[:600]}")


def report(capture_dir: Path) -> None:
    files = sorted(capture_dir.glob("*.json"))
    if not files:
        print(f"No captures in {capture_dir}")
        return
    rows = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        fn = (d.get("headers") or {}).get("X-EgoNet-Function") or d.get("path", "?")
        rows.append((f.name[:15], fn, d))  # name prefix = YYYYmmdd-HHMMSS
    rows.sort(key=lambda r: r[0])
    print(f"=== {len(rows)} captured requests in {capture_dir} ===\n")
    for ts, fn, d in rows:
        b = d.get("decoded_body") or {}
        key = " ".join(
            f"{k}={u(b[k])}"
            for k in ("ChallengeId", "EventIndex", "StageIndex", "ClubId")
            if k in b
        )
        print(f"{ts}  {fn}  {key}")
        if fn == "RaceNetChallenges.GetStageSplits":
            dump_splits(d)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", action="store_true",
                        help="analyze captured traffic instead of running the server")
    parser.add_argument("--restore", action="store_true",
                        help="remove the hosts redirect entries and exit")
    parser.add_argument("--upstream-ip", default=None,
                        help=f"real EGO net IP (default: {dr3_app.UPSTREAM_IP})")
    parser.add_argument("--capture-dir", default=str(ROOT / "build" / "upstream_split_captures"),
                        help="where request captures are written/read")
    args = parser.parse_args()
    capture_dir = Path(args.capture_dir)

    if args.restore:
        if not dr3_app.is_admin():
            print("Elevation required to edit the hosts file — re-run from an elevated terminal.")
            return 1
        dr3_app.remove_hosts()
        print("hosts entries removed.")
        return 0

    upstream_ip = args.upstream_ip or dr3_app.UPSTREAM_IP
    if args.report:
        report(capture_dir)
        return 0

    setup(upstream_ip)
    run_server(upstream_ip, capture_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
