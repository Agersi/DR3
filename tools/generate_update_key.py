"""Generate the Ed25519 update-signing keypair for DR3 client releases.

Run ONCE from the repo root:

    python tools\\generate_update_key.py

Writes (both under tools/keys/, which is gitignored):
    dr3_update_ed25519.key  - private key (PEM). Signs every release zip in
                              build_exe.py. NEVER upload it to the VPS and
                              never commit it.
    dr3_update_ed25519.pub  - public key (PEM). Copy its contents into the
                              UPDATE_PUBLIC_KEY_PEM constant in dr3_app.py
                              before building v0.1.94 or later.

Refuses to overwrite an existing keypair: rotating the key would silently
break every already-shipped client (they embed the old public key), so a
fresh key can only be created after deleting the old files on purpose.

Requires: pip install cryptography   (the build venv has it)
"""
from __future__ import annotations

import sys
from pathlib import Path

KEY_DIR = Path(__file__).resolve().parent / "keys"
PRIVATE_PATH = KEY_DIR / "dr3_update_ed25519.key"
PUBLIC_PATH = KEY_DIR / "dr3_update_ed25519.pub"


def main() -> int:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        print("cryptography is required: pip install cryptography")
        return 1

    if PRIVATE_PATH.exists() or PUBLIC_PATH.exists():
        print(f"Refusing to overwrite an existing keypair in {KEY_DIR}.\n"
              "Rotating the key breaks every shipped client that embeds the\n"
              "old public key. Delete both files first if you really mean it.")
        return 1

    key = Ed25519PrivateKey.generate()
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_PATH.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    PUBLIC_PATH.write_bytes(key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    # Best-effort: strip read access for other local users (Windows ACLs are
    # not exposed by os.chmod; this only helps on POSIX dev boxes).
    try:
        PRIVATE_PATH.chmod(0o600)
    except OSError:
        pass

    print(f"Wrote {PRIVATE_PATH}")
    print(f"Wrote {PUBLIC_PATH}")
    print()
    print("Next: copy the public key below into dr3_app.py as UPDATE_PUBLIC_KEY_PEM")
    print("=" * 72)
    print(PUBLIC_PATH.read_text().strip())
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
