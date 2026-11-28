#!/usr/bin/env bash
# Restore the live client zip from its .bak-<ver> backup after a botched swap
# (deploy_client_swap.sh is NOT idempotent — re-running it moves the live zip
# into the backup and then fails on the missing .new, leaving no live zip).
# Usage: python tools/vps.py --sudo --run-script tools/restore_client_zip.sh OLDVER
set -e
cd /opt/dr3/dist
if [ ! -f DR3-windows.zip ] && [ -f "DR3-windows.zip.bak-$1" ]; then
    mv "DR3-windows.zip.bak-$1" DR3-windows.zip
    echo "restored live zip from .bak-$1"
fi
ls -la DR3-windows.zip
