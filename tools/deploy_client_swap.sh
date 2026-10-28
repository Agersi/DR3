#!/usr/bin/env bash
# Swap in the newly uploaded client zip (DR3-windows.zip.new) on the VPS,
# keeping the previous version as .bak-<oldver>.
# Usage: python tools/vps.py --sudo --run-script tools/deploy_client_swap.sh OLDVER
set -e
cd /opt/dr3/dist
if [ -f DR3-windows.zip ]; then
    mv DR3-windows.zip "DR3-windows.zip.bak-$1"
fi
mv DR3-windows.zip.new DR3-windows.zip
ls -la DR3-windows.zip*
