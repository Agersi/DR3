#!/bin/bash
# S3 diagnostic: verify backup keypair consistency + verbose auth trace to the PC.
echo "=== pubkey derived from private key ==="
ssh-keygen -y -f /root/.ssh/dr3_backup
echo "=== .pub file on disk ==="
cat /root/.ssh/dr3_backup.pub
echo "=== verbose probe (auth lines only) ==="
ssh -vvv -i /root/.ssh/dr3_backup -o BatchMode=yes -o ConnectTimeout=20 dr3backup@serverpc-agersi echo PROBE_OK 2>&1 | grep -E 'identity file|Offering|refused|Authentications that can continue|PROBE_OK' | head -30
