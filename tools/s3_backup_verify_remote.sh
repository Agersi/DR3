#!/bin/bash
# S3: list the off-site backup folder on the PC (remote shell is cmd, so 'dir').
ssh -i /root/.ssh/dr3_backup -o BatchMode=yes -o ConnectTimeout=20 dr3backup@serverpc-agersi 'dir C:\Users\dr3backup\backups\dr3'
