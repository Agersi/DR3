#!/bin/bash
# S3: start tailscale auth in the background, print the login URL.
nohup tailscale up --hostname dr3-vps > /tmp/ts_up.log 2>&1 &
sleep 8
cat /tmp/ts_up.log
