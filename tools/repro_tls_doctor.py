#!/usr/bin/env python3
"""Verify the TLS doctor: Windows cert-store scan + pinned-root fallback.

Background: CPython on Windows builds its default SSL trust store from the
Windows certificate stores (ROOT + CA, machine + user). Expired Let's Encrypt
intermediates imported into those stores make OpenSSL reject perfectly valid
LE-issued server certificates ("certificate has expired") while browsers keep
working.  dr2server.tls_doctor detects that class of problem and falls back
to a context trusting ONLY the embedded public ISRG Root X1/X2 certificates -
verification is never disabled.

Rules under test:
- parse_store_scan / expired_let_encrypt_certs / removal_commands (pure).
- Embedded roots are byte-exact copies of Let's Encrypt's public roots.
- robust_urlopen: healthy path unchanged; on SSLCertVerificationError it logs
  a diagnosis and retries once via the pinned context (which really does
  verify byagersi.com); when the retry also fails the ORIGINAL error
  propagates; non-verification errors never trigger the fallback.
- A self-signed local server is rejected on BOTH paths (the fallback can't
  be used to bypass verification).
- UpstreamClient._get works end-to-end against the live API.

Run: python tools/repro_tls_doctor.py   (from D:\\CODE\\DR2)
"""
import hashlib
import ipaddress
import ssl
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, r"D:\CODE\DR2\dr3")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from dr2server.tls_doctor import (  # noqa: E402
    ISRG_ROOT_X1_PEM, ISRG_ROOT_X2_PEM,
    capture_presented_cert, diagnose_verify_failure, expired_let_encrypt_certs,
    parse_store_scan, pinned_root_context, removal_commands, robust_urlopen,
    scan_windows_cert_store,
)

FAILS = 0


def check(label, got, want):
    global FAILS
    ok = got == want
    print(f"[{'OK' if ok else 'FAIL'}] {label}: got={got!r} want={want!r}")
    if not ok:
        FAILS += 1


def check_true(label, cond):
    check(label, bool(cond), True)


# ---------------------------------------------------------------------------
# 1. parse_store_scan (pure)
# ---------------------------------------------------------------------------
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)

SAMPLE = "\n".join([
    "CurrentUser\\CA|CN=R3, O=Let's Encrypt, C=US|CN=Baltimore CyberTrust Root, O=Baltimore, OU=cybertrust, C=US|2025-09-15T18:00:00|AAAA1111",
    "LocalMachine\\Root|CN=ISRG Root X1, O=Internet Security Research Group, C=US|CN=ISRG Root X1, O=Internet Security Research Group, C=US|2035-06-04T11:04:38|BBBB2222",
    "bad line without pipes",
    "CurrentUser\\CA|CN=Too|Few|Fields|2025-01-01T00:00:00",
    "LocalMachine\\CA|CN=Bad Date, O=X, C=US|CN=X, O=X, C=US|not-a-date|CCCC3333",
    "",
])

entries = parse_store_scan(SAMPLE)
check("parse: two valid entries survive", len(entries), 2)
check("parse: store field", entries[0]["store"], "CurrentUser\\CA")
check("parse: subject field", entries[0]["subject"], "CN=R3, O=Let's Encrypt, C=US")
check("parse: not_after parsed as UTC", entries[0]["not_after"],
      datetime(2025, 9, 15, 18, 0, 0, tzinfo=timezone.utc))
check("parse: thumbprint field", entries[0]["thumbprint"], "AAAA1111")

# ---------------------------------------------------------------------------
# 2. expired_let_encrypt_certs (pure)
# ---------------------------------------------------------------------------
bad = expired_let_encrypt_certs(entries, now=NOW)
check("filter: exactly the expired LE entry", [e["thumbprint"] for e in bad], ["AAAA1111"])

non_le_expired = {
    "store": "CurrentUser\\CA", "subject": "CN=Some Other CA, O=Other, C=US",
    "issuer": "CN=Root, O=Other, C=US",
    "not_after": datetime(2020, 1, 1, tzinfo=timezone.utc), "thumbprint": "ZZZZ9999"}
check("filter: expired NON-LE cert is not flagged",
      expired_let_encrypt_certs([non_le_expired], now=NOW), [])

# ---------------------------------------------------------------------------
# 3. removal_commands (pure)
# ---------------------------------------------------------------------------
cmds = removal_commands(bad)
check("removal: exact PowerShell command", cmds,
      ['Remove-Item "Cert:\\CurrentUser\\CA\\AAAA1111"'])

# ---------------------------------------------------------------------------
# 4. embedded roots are byte-exact public LE roots
# ---------------------------------------------------------------------------
EXPECTED_FP = {
    "X1": "96BCEC06264976F37460779ACF28C5A7CFE8A3C0AAE11A8FFCEE05C0BDDF08C6",
    "X2": "69729B8E15A86EFC177A57AFB7171DFC64ADD28C2FCA8CF1507E34453CCB1470",
}
for name, pem in (("X1", ISRG_ROOT_X1_PEM), ("X2", ISRG_ROOT_X2_PEM)):
    der = x509.load_pem_x509_certificate(pem.encode()).public_bytes(Encoding.DER)
    check(f"embedded ISRG Root {name} fingerprint",
          hashlib.sha256(der).hexdigest().upper(), EXPECTED_FP[name])

ctx = pinned_root_context()
check("pinned ctx: verification enforced", (ctx.verify_mode, ctx.check_hostname),
      (ssl.CERT_REQUIRED, True))

# ---------------------------------------------------------------------------
# 5. live byagersi.com: presented cert + healthy path
# ---------------------------------------------------------------------------
presented = capture_presented_cert("byagersi.com")
check_true("live: presented cert captured", presented is not None)
if presented:
    check_true("live: issuer is Let's Encrypt", "Let" in presented.get("issuer", ""))
    check_true("live: presented cert currently valid",
               presented.get("not_after", datetime(1970, 1, 1, tzinfo=timezone.utc)) > NOW)

req = urllib.request.Request("https://byagersi.com/rally/api/client-version")
with robust_urlopen(req, timeout=15) as resp:
    check("live: healthy path returns 200", resp.status, 200)

# ---------------------------------------------------------------------------
# 6. simulated verification failure -> pinned retry succeeds (live)
# ---------------------------------------------------------------------------
import dr2server.tls_doctor as td  # noqa: E402

calls = []
real_urlopen = urllib.request.urlopen


def flaky_urlopen(r, timeout=None, **kw):
    calls.append(kw.get("context"))
    if len(calls) == 1:
        raise urllib.error.URLError(ssl.SSLCertVerificationError(1856))
    return real_urlopen(r, timeout=timeout, **kw)


urllib.request.urlopen = flaky_urlopen
try:
    with robust_urlopen(urllib.request.Request(
            "https://byagersi.com/rally/api/client-version"), timeout=15) as resp:
        check("fallback: retry succeeds against live API", resp.status, 200)
finally:
    urllib.request.urlopen = real_urlopen
check("fallback: first call used OS context, second the pinned one",
      (calls[0] is None, calls[1] is not None and calls[1].verify_mode == ssl.CERT_REQUIRED),
      (True, True))

# ---------------------------------------------------------------------------
# 7. simulated failure -> retry also fails -> original error propagates
# ---------------------------------------------------------------------------
def always_fail(r, timeout=None, **kw):
    raise urllib.error.URLError(ssl.SSLCertVerificationError(1856))


urllib.request.urlopen = always_fail
try:
    try:
        robust_urlopen(urllib.request.Request("https://byagersi.com/rally/x"), timeout=5)
        check("double-fail: original URLError propagates", "no error", "URLError")
    except urllib.error.URLError as exc:
        check("double-fail: original URLError propagates",
              isinstance(exc.reason, ssl.SSLCertVerificationError), True)
finally:
    urllib.request.urlopen = real_urlopen

# ---------------------------------------------------------------------------
# 8. non-verification errors never trigger the fallback
# ---------------------------------------------------------------------------
def refused(r, timeout=None, **kw):
    raise urllib.error.URLError(ConnectionRefusedError(10061))


urllib.request.urlopen = refused
try:
    try:
        robust_urlopen(urllib.request.Request("https://byagersi.com/rally/x"), timeout=5)
        check("refused: error propagates untouched", "no error", "URLError")
    except urllib.error.URLError as exc:
        check("refused: error propagates untouched",
              isinstance(exc.reason, ConnectionRefusedError), True)
finally:
    urllib.request.urlopen = real_urlopen

# ---------------------------------------------------------------------------
# 9. self-signed local server is rejected on BOTH paths (no bypass)
# ---------------------------------------------------------------------------
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.hazmat.primitives.serialization import (  # noqa: E402
    NoEncryption as _NoEncryption, PrivateFormat as _PF)


def make_self_signed():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOW - timedelta(days=1))
            .not_valid_after(NOW + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
    key_pem = key.private_bytes(Encoding.PEM, _PF.TraditionalOpenSSL,
                                _NoEncryption())
    return key_pem, cert.public_bytes(Encoding.PEM).decode()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


from pathlib import Path as _Path  # noqa: E402

key_pem, cert_pem = make_self_signed()
# this Python's load_cert_chain needs real files, not data kwargs; keep the
# scratch PEMs inside the project (AGENTS.md: no temp dirs outside it)
_scratch = _Path(__file__).resolve().parents[1] / "build" / "tls_selftest"
_scratch.mkdir(parents=True, exist_ok=True)
_key_path = _scratch / "key.pem"
_cert_path = _scratch / "cert.pem"
_key_path.write_bytes(key_pem)
_cert_path.write_text(cert_pem, encoding="utf-8")

server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
sctx.load_cert_chain(certfile=str(_cert_path), keyfile=str(_key_path))
server.socket = sctx.wrap_socket(server.socket, server_side=True)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

try:
    try:
        robust_urlopen(urllib.request.Request(f"https://127.0.0.1:{port}/"), timeout=5)
        check("self-signed: rejected (no bypass via fallback)", "connected", "rejected")
    except urllib.error.URLError as exc:
        check_true("self-signed: rejected with a verification error",
                   isinstance(exc.reason, ssl.SSLError))
finally:
    server.shutdown()
    try:
        _key_path.unlink(missing_ok=True)
        _cert_path.unlink(missing_ok=True)
        _scratch.rmdir()
    except OSError:
        pass

# ---------------------------------------------------------------------------
# 10. live PowerShell store scan on this machine (informational)
# ---------------------------------------------------------------------------
live_entries = scan_windows_cert_store(refresh=True)
check_true("store scan: returns a list", isinstance(live_entries, list))
for e in live_entries:
    check_true(f"store scan entry well-formed ({e['subject'][:40]})",
               all(k in e for k in ("store", "subject", "issuer", "not_after", "thumbprint")))
live_bad = expired_let_encrypt_certs(live_entries)
print(f"[INFO] this machine's store: {len(live_entries)} LE/ISRG entries, "
      f"{len(live_bad)} expired")

# ---------------------------------------------------------------------------
# 11. diagnose_verify_failure builds actionable lines (offline shape check)
# ---------------------------------------------------------------------------
lines = diagnose_verify_failure("https://byagersi.com/rally/api/game/clubs",
                                ssl.SSLCertVerificationError(1856))
check_true("diagnose: multiple lines", len(lines) >= 3)
check_true("diagnose: points at the install page",
           any("install#cert-troubleshooting" in l for l in lines))
check_true("diagnose: mentions local system time",
           any("Local system time" in l for l in lines))

# ---------------------------------------------------------------------------
# 12. UpstreamClient end-to-end through robust_urlopen (live)
# ---------------------------------------------------------------------------
from dr2server.api_client import UpstreamClient  # noqa: E402

client = UpstreamClient(base_url="https://byagersi.com/rally")
data = client._get("/api/client-version")
check_true("api_client e2e: /api/client-version returns a dict",
           isinstance(data, dict) and "version" in data)

print()
if FAILS:
    print(f"TLS DOCTOR REPRO FAILED: {FAILS} failure(s)")
    sys.exit(1)
print("ALL TLS DOCTOR CHECKS PASSED")
