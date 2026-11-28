"""TLS health diagnostics + resilient HTTPS for outbound DR3 API calls.

Background: CPython on Windows builds its default SSL trust store by
enumerating the Windows certificate stores (ROOT and CA, machine-wide AND
per-user).  If a user ever imported old Let's Encrypt intermediates into
their store (a common "fix my TLS" workaround) and those certs have since
expired, OpenSSL verification of perfectly valid LE-issued server
certificates can fail with ``certificate has expired`` - while browsers
(schannel) keep working because they pick the right anchor.  The symptom in
DR3: no clubs appear in-game, debug.log shows an SSL error on byagersi.com.

This module provides:

* ``scan_windows_cert_store()`` / ``parse_store_scan()`` - list Let's
  Encrypt / ISRG entries in the four Windows cert stores with expiry dates.
* ``expired_let_encrypt_certs()`` - filter to the entries that break Python
  TLS (the exact class of problem above).
* ``capture_presented_cert()`` - what the server actually presents
  (subject/issuer/expiry) for diagnostics.
* ``pinned_root_context()`` - a clean SSL context trusting ONLY the embedded
  public ISRG Root X1/X2 certificates.  Verification is never disabled; this
  just swaps in a known-good trust anchor set when the OS store is polluted.
* ``robust_urlopen()`` - the single entry point for outbound HTTPS: try the
  default (OS-store) context first, and on a certificate-verification failure
  log an actionable diagnosis and retry once via the pinned-root context.
* ``verify_upstream_leaf()`` - cryptographic verification of the real EgoNet
  upstream's leaf certificate against the embedded Codemasters root CA.

The embedded roots are Let's Encrypt's public root certificates:
  ISRG Root X1: https://letsencrypt.org/certs/isrgrootx1.pem
      sha256 96BCEC06264976F37460779ACF28C5A7CFE8A3C0AAE11A8FFCEE05C0BDDF08C6
  ISRG Root X2: https://letsencrypt.org/certs/isrg-root-x2.pem
      sha256 69729B8E15A86EFC177A57AFB7171DFC64ADD28C2FCA8CF1507E34453CCB1470

and Codemasters' private internal root (``CM_ROOT_CA_PEM``):
  CN=Codemasters Online Root CA, OU=Codemasters Online,
  O=Codemasters Software Ltd - self-signed, valid 2010-09-22 .. 2035-09-16.
  Extracted from the DR2 game binary (``dirtrally2.exe``) with
  ``tools/extract_cm_root.py``; the game trusts it for its own EgoNet TLS.
  The upstream leaf is CN-only (no SAN) and signed SHA-1 by this root, so
  OpenSSL's built-in chain verification rejects it ("CA signature digest
  algorithm too weak") and CPython exposes no way to lower the security
  level - hence ``verify_upstream_leaf`` performs the checks itself.
"""
from __future__ import annotations

import logging
import socket
import ssl
import subprocess
import threading
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, List, Optional

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import NameOID

log = logging.getLogger(__name__)

ISRG_ROOT_X1_PEM = """\
-----BEGIN CERTIFICATE-----
MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw
TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh
cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4
WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu
ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY
MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc
h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+
0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U
A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW
T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH
B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC
B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv
KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn
OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn
jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw
qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI
rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq
hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL
ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ
3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK
NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5
ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur
TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC
jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc
oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq
4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA
mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d
emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=
-----END CERTIFICATE-----
"""

ISRG_ROOT_X2_PEM = """\
-----BEGIN CERTIFICATE-----
MIICGzCCAaGgAwIBAgIQQdKd0XLq7qeAwSxs6S+HUjAKBggqhkjOPQQDAzBPMQsw
CQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJuZXQgU2VjdXJpdHkgUmVzZWFyY2gg
R3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBYMjAeFw0yMDA5MDQwMDAwMDBaFw00
MDA5MTcxNjAwMDBaME8xCzAJBgNVBAYTAlVTMSkwJwYDVQQKEyBJbnRlcm5ldCBT
ZWN1cml0eSBSZXNlYXJjaCBHcm91cDEVMBMGA1UEAxMMSVNSRyBSb290IFgyMHYw
EAYHKoZIzj0CAQYFK4EEACIDYgAEzZvVn4CDCuwJSvMWSj5cz3es3mcFDR0HttwW
+1qLFNvicWDEukWVEYmO6gbf9yoWHKS5xcUy4APgHoIYOIvXRdgKam7mAHf7AlF9
ItgKbppbd9/w+kHsOdx1ymgHDB/qo0IwQDAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0T
AQH/BAUwAwEB/zAdBgNVHQ4EFgQUfEKWrt5LSDv6kviejM9ti6lyN5UwCgYIKoZI
zj0EAwMDaAAwZQIwe3lORlCEwkSHRhtFcP9Ymd70/aTSVaYgLXTWNLxBo1BfASdW
tL4ndQavEi51mI38AjEAi/V3bNTIZargCyzuFJ0nN6T5U6VR5CmD1/iQMVtCnwr1
/q4AaOeMSQ+2b1tbFfLn
-----END CERTIFICATE-----
"""

# Codemasters' private root CA for the EgoNet backend (prod.egonet.codemasters.com).
# Extracted from dirtrally2.exe (the DR2 client embeds it for its own TLS); see
# tools/extract_cm_root.py. Self-signed RSA-2048, valid 2010-09-22 .. 2035-09-16.
CM_ROOT_CA_PEM = """\
-----BEGIN CERTIFICATE-----
MIID5zCCAs+gAwIBAgIJAIIfD20HgCPEMA0GCSqGSIb3DQEBBQUAMIGJMQswCQYD
VQQGEwJVSzEVMBMGA1UECAwMV2Fyd2lja3NoaXJlMSEwHwYDVQQKDBhDb2RlbWFz
dGVycyBTb2Z0d2FyZSBMdGQxGzAZBgNVBAsMEkNvZGVtYXN0ZXJzIE9ubGluZTEj
MCEGA1UEAwwaQ29kZW1hc3RlcnMgT25saW5lIFJvb3QgQ0EwHhcNMTAwOTIyMDgx
NjA4WhcNMzUwOTE2MDgxNjA4WjCBiTELMAkGA1UEBhMCVUsxFTATBgNVBAgMDFdh
cndpY2tzaGlyZTEhMB8GA1UECgwYQ29kZW1hc3RlcnMgU29mdHdhcmUgTHRkMRsw
GQYDVQQLDBJDb2RlbWFzdGVycyBPbmxpbmUxIzAhBgNVBAMMGkNvZGVtYXN0ZXJz
IE9ubGluZSBSb290IENBMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA
ncw3VeQt3N8ZJmNGiCa8UJ61qtBWxat2yx9bWfyf2o4VqQtn7Cc79esHkf5Zbjpc
zqMpxO/c0vZnHWgH1R0TWRDrSqKHuJxgbxT2JFoLAQQKm+uTUOclLvlC00IrINks
Y6KPYaQDkaLDAQWX42YGqb7CaKna8DwX6Ms4RHLEm5+L0L5GgRk9RfPlphVYCA9r
tjJIQluYMc0Ny4tzJ6IuCnDjgRfmehWSpBV+UAx8FHFxmOUgone6dhA0pTLtR/lu
P/S2aISWgQZJ0GWWfQ/mjVpccnWKtZUK0TCVoKTPpGaV4kTDHYq9ylpuNItTww9E
rXR0/9gWOZHQ49QXd2rnrQIDAQABo1AwTjAdBgNVHQ4EFgQU9vW1MrzNfAbZeTQJ
gHCzyQQlAZgwHwYDVR0jBBgwFoAU9vW1MrzNfAbZeTQJgHCzyQQlAZgwDAYDVR0T
BAUwAwEB/zANBgkqhkiG9w0BAQUFAAOCAQEAcOUnPyvrbAdzMSPN9PLnvh34I9jW
EwU81ks67EBNHaMdghiUhOFZ65vzQLoUYoqQcgzu+i0rAkLZrfviC1TUlv+mdlgK
ce6eD2VgpQOPrpOG6O/TfmyAhS5mOlA35NMsZqZTpIiTZnZLGHSSu/Shvnk7UDTs
lTp1yNj6etlY03ABieBsSIDu0UmhNfEY2HgjPaGGiAmcenw2d4U5Z2oCiJVDseeq
RdlVx/sg4UM8L24/ccqn5uV05cg5aUF5dAlAYLq2wXNgbkpePtmo+/tqdiE3pL+q
TRW14lealjJNmjUUTO/KHjKOpfPJNCHSGLk/h/UskEvcTUTqwDZATCl4jw==
-----END CERTIFICATE-----
"""

UPSTREAM_HOST = "prod.egonet.codemasters.com"

_STORE_SCAN_PS = (
    "foreach ($s in 'LocalMachine\\Root','LocalMachine\\CA',"
    "'CurrentUser\\Root','CurrentUser\\CA') { Get-ChildItem \"Cert:\\$s\" "
    "-ErrorAction SilentlyContinue | Where-Object { $_.Subject -match "
    "\"Let|ISRG\" } | ForEach-Object { Write-Output ($s + '|' + $_.Subject + "
    "'|' + $_.Issuer + '|' + $_.NotAfter.ToUniversalTime().ToString("
    "'yyyy-MM-ddTHH:mm:ss') + '|' + $_.Thumbprint) } }"
)

_scan_cache: Dict[str, object] = {"at": None, "entries": []}
_scan_lock = threading.Lock()
_pinned_ctx: Optional[ssl.SSLContext] = None


def parse_store_scan(text: str) -> List[Dict]:
    """Parse the pipe-delimited output of ``_STORE_SCAN_PS``.

    One entry per line: ``store|subject|issuer|notAfterUTC|thumbprint``.
    Malformed lines are skipped."""
    entries: List[Dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 5:
            continue
        store, subject, issuer, date_str, thumbprint = parts
        try:
            not_after = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
        entries.append({
            "store": store.strip(),
            "subject": subject.strip(),
            "issuer": issuer.strip(),
            "not_after": not_after,
            "thumbprint": thumbprint.strip(),
        })
    return entries


def scan_windows_cert_store(timeout: float = 15.0, refresh: bool = False) -> List[Dict]:
    """List Let's Encrypt / ISRG entries in the four Windows cert stores.

    Returns [] on non-Windows platforms or when PowerShell is unavailable /
    times out (the scan is best-effort diagnostics, never fatal).  Results
    are cached for five minutes so a burst of failing requests does not
    re-shell out every time."""
    global _scan_cache
    with _scan_lock:
        now = datetime.now(timezone.utc)
        if (not refresh and _scan_cache["at"] is not None
                and (now - _scan_cache["at"]).total_seconds() < 300):
            return list(_scan_cache["entries"])  # type: ignore[arg-type]
    if not _is_windows():
        return []
    try:
        # CREATE_NO_WINDOW: the GUI client must not flash a console window
        # for the background cert-store scan.
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-Command", _STORE_SCAN_PS],
            capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    entries = parse_store_scan(proc.stdout or "")
    with _scan_lock:
        _scan_cache["at"] = now
        _scan_cache["entries"] = entries
    return entries


def _is_windows() -> bool:
    import platform
    return platform.system() == "Windows"


def expired_let_encrypt_certs(entries: List[Dict],
                              now: Optional[datetime] = None) -> List[Dict]:
    """Filter store entries to EXPIRED Let's Encrypt / ISRG certificates.

    These are the entries that poison CPython's Windows trust store and make
    OpenSSL reject perfectly valid LE-issued server certificates."""
    if now is None:
        now = datetime.now(timezone.utc)
    out = []
    for e in entries:
        subject = e.get("subject", "")
        if "Let" not in subject and "ISRG" not in subject:
            continue
        if e["not_after"] < now:
            out.append(e)
    return out


def removal_commands(entries: List[Dict]) -> List[str]:
    """Exact PowerShell commands to remove the given store entries."""
    cmds = []
    for e in entries:
        cmds.append(f'Remove-Item "Cert:\\{e["store"]}\\{e["thumbprint"]}"')
    return cmds


def pinned_root_context() -> ssl.SSLContext:
    """SSL context trusting only the embedded ISRG Root X1/X2 certificates.

    Full verification is still enforced (hostname check + chain validation) -
    this simply replaces the possibly-polluted OS trust store with LE's
    public roots, so a wrong or forged certificate is rejected exactly as
    before."""
    global _pinned_ctx
    if _pinned_ctx is None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=ISRG_ROOT_X1_PEM + ISRG_ROOT_X2_PEM)
        _pinned_ctx = ctx
    return _pinned_ctx


_cm_root: Optional[x509.Certificate] = None


def _load_cm_root() -> x509.Certificate:
    global _cm_root
    if _cm_root is None:
        _cm_root = x509.load_pem_x509_certificate(CM_ROOT_CA_PEM.encode("ascii"))
    return _cm_root


def verify_upstream_leaf(leaf_der: bytes, expected_host: str = UPSTREAM_HOST) -> None:
    """Cryptographically verify the EgoNet upstream's leaf certificate.

    OpenSSL (via CPython ssl) cannot verify this chain: CM's internal root
    signs with SHA-1, which OpenSSL 3 rejects ("CA signature digest algorithm
    too weak"), and CPython exposes no way to lower the security level. So we
    perform the checks ourselves against the embedded root - the same one the
    game binary trusts for its own EgoNet TLS:

      1. issuer-name binding (leaf.issuer == root.subject)
      2. signature verification with the certificate's own hash algorithm
      3. validity window
      4. hostname coverage (SAN preferred; CM's leaf is CN-only, so fall back
         to the subject CN - the RFC 2818 legacy path)

    Raises ssl.SSLCertVerificationError on any failure; callers treat that as
    "upstream unreachable" and apply their offline fallback."""
    root = _load_cm_root()
    try:
        leaf = x509.load_der_x509_certificate(leaf_der)
    except ValueError as exc:
        raise ssl.SSLCertVerificationError(
            f"unparseable upstream certificate: {exc}") from exc

    if leaf.issuer != root.subject:
        raise ssl.SSLCertVerificationError(
            "upstream certificate is not issued by the embedded Codemasters root")

    hash_algo = leaf.signature_hash_algorithm
    if hash_algo is None:
        raise ssl.SSLCertVerificationError(
            "upstream certificate carries no signature hash algorithm")
    try:
        pub = root.public_key()
        if isinstance(pub, rsa.RSAPublicKey):
            pub.verify(leaf.signature, leaf.tbs_certificate_bytes,
                       padding.PKCS1v15(), hash_algo)
        elif isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(leaf.signature, leaf.tbs_certificate_bytes,
                       ec.ECDSA(hash_algo))
        else:
            raise ssl.SSLCertVerificationError(
                f"unsupported root key type: {type(pub).__name__}")
    except InvalidSignature as exc:
        raise ssl.SSLCertVerificationError(
            "upstream certificate signature does not verify against the embedded root") from exc

    now = datetime.now(timezone.utc)
    try:
        not_before, not_after = leaf.not_valid_before_utc, leaf.not_valid_after_utc
    except AttributeError:  # cryptography < 42
        not_before = leaf.not_valid_before.replace(tzinfo=timezone.utc)
        not_after = leaf.not_valid_after.replace(tzinfo=timezone.utc)
    if not (not_before <= now <= not_after):
        raise ssl.SSLCertVerificationError(
            f"upstream certificate outside validity window "
            f"({not_before:%Y-%m-%d} .. {not_after:%Y-%m-%d})")

    try:
        names = leaf.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value.get_values_for_name(x509.DNSName)
    except x509.ExtensionNotFound:
        names = [v.value for v in leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)]
    if expected_host.lower() not in (n.lower() for n in names):
        raise ssl.SSLCertVerificationError(
            f"upstream certificate does not cover {expected_host} "
            f"(covers: {', '.join(names) or 'nothing'})")


def capture_presented_cert(host: str, port: int = 443,
                           timeout: float = 10.0) -> Optional[Dict]:
    """Connect without verification and describe the presented certificate.

    Used only for diagnostics after a verification failure - tells the user
    whether the SERVER side is actually healthy."""
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError):
        return None
    if not der:
        return None
    info: Dict = {"der_length": len(der)}
    try:
        from cryptography import x509
        cert = x509.load_der_x509_certificate(der)
        info["subject"] = cert.subject.rfc4514_string()
        info["issuer"] = cert.issuer.rfc4514_string()
        try:
            not_after = cert.not_valid_after_utc
        except AttributeError:  # older cryptography
            not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
        info["not_after"] = not_after
    except Exception:
        pass
    return info


def diagnose_verify_failure(url: str, reason: ssl.SSLError) -> List[str]:
    """Build the actionable log lines shown when OS-store verification fails."""
    host = urllib.parse.urlparse(url).hostname or url
    lines = [
        f"TLS verification failed for {host}: {reason}",
        "The server certificate is almost certainly fine - this is usually a "
        "local Windows certificate store problem (see "
        "https://byagersi.com/rally/install#cert-troubleshooting).",
    ]
    presented = capture_presented_cert(host)
    if presented:
        not_after = presented.get("not_after")
        if not_after is not None:
            state = ("VALID" if not_after > datetime.now(timezone.utc)
                     else "EXPIRED")
            lines.append(
                f"The server presents a {state} certificate "
                f"({presented.get('issuer', 'unknown issuer')}, expires "
                f"{not_after.strftime('%Y-%m-%d')}).")
        else:
            lines.append(f"The server presented a certificate "
                         f"({presented.get('issuer', 'unknown issuer')}).")
    lines.append(f"Local system time: {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z} "
                 "- if this looks wrong, fix your Windows date and time first.")
    entries = scan_windows_cert_store()
    bad = expired_let_encrypt_certs(entries)
    if bad:
        lines.append(
            "Your Windows certificate store contains EXPIRED Let's Encrypt "
            f"certificates ({len(bad)} found) that confuse Python's TLS:")
        for e in bad:
            lines.append(f"  - {e['subject']} (expired "
                         f"{e['not_after']:%Y-%m-%d}) in {e['store']}")
        lines.append("Remove them with (run each line in PowerShell):")
        lines.extend("  " + c for c in removal_commands(bad))
    else:
        lines.append(
            "No expired Let's Encrypt certificates were found in your Windows "
            "certificate stores - if the problem persists, check that your "
            "system clock is correct and that no security software is "
            "interfering with TLS.")
    return lines


def robust_urlopen(req: urllib.request.Request, timeout: float = 10.0):
    """urllib.request.urlopen with a pinned-root fallback on verify failure.

    Tries the default (OS trust store) context first; only if certificate
    VERIFICATION fails does it log an actionable diagnosis and retry once
    against DR3's embedded Let's Encrypt roots.  Any other error propagates
    unchanged."""
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if not isinstance(reason, ssl.SSLCertVerificationError):
            raise
        for line in diagnose_verify_failure(req.full_url, reason):
            log.warning("%s", line)
        try:
            resp = urllib.request.urlopen(
                req, timeout=timeout, context=pinned_root_context())
        except Exception as retry_exc:
            log.error("TLS retry with DR3's built-in Let's Encrypt roots also "
                      "failed: %s", retry_exc)
            raise exc from retry_exc
        log.warning(
            "Reconnected using DR3's built-in Let's Encrypt root certificates - "
            "the connection continues, but your Windows certificate store is "
            "polluted and should be cleaned up as described above.")
        return resp
