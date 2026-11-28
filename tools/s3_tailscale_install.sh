#!/bin/bash
# S3 (Phase 3): install Tailscale on the VPS. Additive — new binary + service only.
set -euo pipefail
curl -fsSL https://tailscale.com/install.sh | sh
systemctl enable --now tailscaled
tailscale version
