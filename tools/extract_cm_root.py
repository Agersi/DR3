"""Extract the Codemasters Online Root CA certificate from the DR2 game binary.

The game embeds CM's private root CA in DER form (no standalone .cer file
ships with the install). C5 (verified upstream TLS) needs this root to build
a trust context for prod.egonet.codemasters.com, whose leaf is CN-only and
issued by this internal CA.

Usage: python tools\extract_cm_root.py <path-to-dirtrally2.exe> [out.pem]
"""
import sys
from pathlib import Path

ANCHOR = "Codemasters Online Root CA".encode("ascii")


def find_cert_candidates(data: bytes, anchor_idx: int, window: int = 8192):
    """Yield (offset, length) of every `30 82 xx xx` SEQUENCE header in the
    window before the anchor string that parses as a plausible DER cert."""
    lo = max(0, anchor_idx - window)
    for i in range(lo, anchor_idx):
        if data[i] != 0x30 or (i + 3 >= len(data)) or data[i + 1] != 0x82:
            continue
        length = (data[i + 2] << 8) | data[i + 3]
        if not (512 <= length <= 4096):
            continue
        yield i, length


def main() -> int:
    exe = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("build/cm_root_ca.pem")
    data = exe.read_bytes()

    idx = 0
    found = 0
    while True:
        idx = data.find(ANCHOR, idx)
        if idx < 0:
            break
        for off, length in find_cert_candidates(data, idx):
            blob = data[off:off + 4 + length]
            try:
                from cryptography import x509
                cert = x509.load_der_x509_certificate(blob)
            except Exception:
                continue
            subject = cert.subject.rfc4514_string()
            issuer = cert.issuer.rfc4514_string()
            print(f"candidate @ {off}: len={length} subject={subject}")
            print(f"  issuer={issuer}")
            try:
                na = cert.not_valid_after_utc
            except AttributeError:
                na = None
            print(f"  notAfter={na}")
            if "Root CA" in subject and found == 0:
                out.parent.mkdir(parents=True, exist_ok=True)
                from cryptography.hazmat.primitives import serialization
                out.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
                print(f"  -> wrote {out}")
                found += 1
        idx += len(ANCHOR)

    if not found:
        print("no parseable root CA certificate found")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
