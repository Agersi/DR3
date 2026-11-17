"""Verify the elevated-helper exit-code contract in dr3_app.py.

The parent reads the elevated child's exit code from two different sources:
  * the helper's own return codes (ADMIN_HELPER_OK/BAD_OP/EXCEPTION/CERT_FAILED)
  * launch failures reported as LAUNCH_FAILED_BASE + Win32 error code
If the two ranges ever overlap, a failed UAC launch (e.g. Win32 error 2 =
ERROR_FILE_NOT_FOUND when the exe sits in a synced/protected folder like
Downloads) would be misreported as a helper crash - the exact "helper exit 2"
symptom from the Nov 2026 user report.

Usage: tools\verify_admin_helper_contract.py [path-to-dr3_app.py]
"""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "dr3" / "dr3_app.py"
    spec = importlib.util.spec_from_file_location("dr3_app", target)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    helper_codes = {
        "ADMIN_HELPER_OK": mod.ADMIN_HELPER_OK,
        "ADMIN_HELPER_BAD_OP": mod.ADMIN_HELPER_BAD_OP,
        "ADMIN_HELPER_EXCEPTION": mod.ADMIN_HELPER_EXCEPTION,
        "ADMIN_HELPER_CERT_FAILED": mod.ADMIN_HELPER_CERT_FAILED,
    }
    base = mod.LAUNCH_FAILED_BASE
    fails = []

    if len(set(helper_codes.values())) != len(helper_codes):
        fails.append("helper exit codes are not unique")
    for name, code in helper_codes.items():
        if code >= base:
            fails.append(f"{name}={code} falls inside the launch-failure range (>= {base})")
    # Win32 errors are small ints; reserve headroom above the helper codes.
    if base < max(helper_codes.values()) + 1:
        fails.append(f"LAUNCH_FAILED_BASE={base} leaves no gap above helper codes")

    if fails:
        for f in fails:
            print(f"[FAIL] {f}")
        return 1
    print(f"helper codes: {helper_codes}")
    print(f"launch failures reported as >= {base} (base + Win32 error)")
    print("ADMIN HELPER CONTRACT OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
