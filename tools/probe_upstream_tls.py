r"""Probe the real EgoNet upstream with a *verified* TLS context.

C5 (httpd proxy lockdown) switches the proxy from CERT_NONE to full
verification (ssl.create_default_context defaults: CERT_REQUIRED +
check_hostname). This probe proves the assumption against the live upstream
before we ship it: if CM's chain fails verification on a stock Python install,
every player's proxy would break.

Usage: python tools\probe_upstream_tls.py [upstream_ip]
(default ip = dr3_app.UPSTREAM_IP)
"""
import socket
import ssl
import sys


def main() -> int:
    ip = sys.argv[1] if len(sys.argv) > 1 else "159.153.126.42"
    sni = "prod.egonet.codemasters.com"

    ctx = ssl.create_default_context()
    print(f"verify_mode={ctx.verify_mode} check_hostname={ctx.check_hostname}")

    try:
        with socket.create_connection((ip, 443), timeout=15) as sock:
            with ctx.wrap_socket(sock, server_hostname=sni) as tls:
                cert = tls.getpeercert()
                print("VERIFIED OK")
                print(f"peer={tls.peername}")
                print(f"version={tls.version()}")
                for k in ("subjectAltName", "issuer", "notAfter"):
                    print(f"{k}={cert.get(k)}")
    except ssl.SSLCertVerificationError as exc:
        print(f"VERIFICATION FAILED: {exc}")
        return 1
    except OSError as exc:
        print(f"CONNECTION FAILED (network?): {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
