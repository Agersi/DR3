"""C1 cert-hardening unit checks (Phase 2). Run: python tools/test_cert_rotation.py
Exercises generate_cert / cert_needs_regen / _cert_thumbprint / remove_dr3_trust
against throwaway temp files — never touches the real store or DR3 dir."""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dr3"))
import dr3_app as app  # noqa: E402

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

tmp = Path(tempfile.mkdtemp())
app.CERT_PATH = tmp / "c.pem"
app.KEY_PATH = tmp / "k.pem"
app.PREV_CERT_PATH = tmp / "p.pem"

failures = []


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        failures.append(label)


def make_pem(ca: bool, days: int) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "prod.egonet.codemasters.com")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(n).issuer_name(n)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


# --- generate_cert: fresh non-CA 365d cert --------------------------------
app.generate_cert()
c = x509.load_pem_x509_certificate(app.CERT_PATH.read_bytes())
bc = c.extensions.get_extension_for_class(x509.BasicConstraints).value
check("generate_cert ca=False", bc.ca is False)
exp = getattr(c, "not_valid_after_utc", None) or c.not_valid_after.replace(tzinfo=timezone.utc)
age_days = (exp - datetime.now(timezone.utc)).days
check(f"generate_cert ~365d validity ({age_days}d)", 360 <= age_days <= 366)

# --- _cert_thumbprint ------------------------------------------------------
import hashlib  # noqa: E402
der = c.public_bytes(serialization.Encoding.DER)
expected = hashlib.sha1(der).hexdigest().upper()
check("thumbprint matches sha1(DER)", app._cert_thumbprint(app.CERT_PATH) == expected)
check("thumbprint missing file -> None", app._cert_thumbprint(tmp / "nope.pem") is None)
app.CERT_PATH.write_bytes(b"garbage")
check("thumbprint corrupt pem -> None", app._cert_thumbprint(app.CERT_PATH) is None)

# --- cert_needs_regen ------------------------------------------------------
app.CERT_PATH.write_bytes(make_pem(ca=False, days=365))
app.KEY_PATH.write_bytes(b"x")
check("fresh ca=False -> keep", app.cert_needs_regen() is False)
app.CERT_PATH.write_bytes(make_pem(ca=True, days=3650))
check("legacy ca=True (10y left) -> migrate", app.cert_needs_regen() is True)
app.CERT_PATH.write_bytes(make_pem(ca=False, days=10))
check("expires in 10d -> regen", app.cert_needs_regen() is True)
app.CERT_PATH.write_bytes(b"garbage")
check("corrupt pem -> regen", app.cert_needs_regen() is True)
app.CERT_PATH.unlink()
check("missing cert -> regen", app.cert_needs_regen() is True)

# --- remove_dr3_trust ------------------------------------------------------
# Fresh random cert: its registry key does not exist -> FileNotFoundError -> True
app.CERT_PATH.write_bytes(make_pem(ca=False, days=365))
check("remove_dr3_trust key-absent -> True", app.remove_dr3_trust() is True)
# PermissionError path (non-elevated HKLM write) must map to False, no crash
import winreg  # noqa: E402
_real_delete = winreg.DeleteKey


def _deny(hive, key):
    raise PermissionError(13, "access denied")


winreg.DeleteKey = _deny
try:
    check("remove_dr3_trust permission-denied -> False", app.remove_dr3_trust() is False)
finally:
    winreg.DeleteKey = _real_delete
app.CERT_PATH.unlink()
check("remove_dr3_trust no cert -> False", app.remove_dr3_trust() is False)

print()
if failures:
    print(f"{len(failures)} FAILURES")
    sys.exit(1)
print("ALL CERT ROTATION CHECKS PASSED")
