#!/bin/bash
# S3 (Phase 3, 2026-11-28): verify the hardened cron setup.
set -e
/usr/local/bin/dr3-cron.sh && echo APP_CRON_OK
ls -la /opt/dr3/web/.env
su -s /bin/sh agersi -c 'grep -m1 -q ^CRON_API_KEY= /opt/dr3/web/.env && echo CRON_ENV_READABLE || echo CRON_ENV_BLOCKED' # post-S3 expect CRON_ENV_BLOCKED
cat /etc/cron.d/dr3-cron /etc/cron.d/dr3-backup
