#!/usr/bin/env bash
# Re-sync /opt/dr3/dist/VERSION.txt from the deployed client zip so the
# /api/client-version endpoint (the client's update banner) always reports
# the version that is ACTUALLY being served. The version is read from DR3/VERSION
# inside DR3-windows.zip — no manual version arg, so it cannot drift from the
# live build (this is what happened when 0.1.70/0.1.71 shipped with a stale
# 0.1.69 marker and clients at 0.1.69 saw "no update").
# Idempotent. Usage: python tools/vps.py --run-script tools/set_client_version_from_zip.sh
set -e
cd /opt/dr3/dist
python3 - <<'PY'
import zipfile

with zipfile.ZipFile("DR3-windows.zip") as z:
    names = [n for n in z.namelist() if n == "VERSION" or n.lower().endswith("/version")]
    if not names:
        raise SystemExit("ERROR: no VERSION entry found in DR3-windows.zip")
    ver = z.read(names[0]).decode().strip()
if not ver:
    raise SystemExit(f"ERROR: {names[0]} in the zip is empty")
with open("VERSION.txt", "w") as f:
    f.write(ver + "\n")
print(f"zip {names[0]} = {ver}  ->  VERSION.txt updated")
PY
