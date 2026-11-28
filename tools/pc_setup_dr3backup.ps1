# DR3 backup PC setup - run ONCE as Administrator on the always-on Windows machine.
# Prerequisite: Tailscale installed and signed in with the SAME account as the VPS
# (https://tailscale.com/download/windows). This script does everything else.
$ErrorActionPreference = 'Stop'

$PubKey  = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAOLw7sW9drZxrRynFMwAncy/R2qlho+OGo9737A2zMQ dr3-backup-vps'
$BkUser  = 'dr3backup'
$Profile = "C:\Users\$BkUser"

# 0. Tailscale sanity check (service name is 'tailscaled' on most installs,
#    'Tailscale' on some — accept either)
$ts = Get-Service -Name 'tailscaled' -ErrorAction SilentlyContinue
if (-not $ts) { $ts = Get-Service -Name 'Tailscale' -ErrorAction SilentlyContinue }
if (-not $ts) {
    Write-Warning 'Tailscale service not found. Install Tailscale first and sign in with the same account as the VPS, then re-run this script.'
    exit 1
}
if ($ts.Status -ne 'Running') { Write-Warning 'Tailscale is installed but not running.' }

# 1. OpenSSH Server (built-in optional feature)
$cap = Get-WindowsCapability -Online -Name openssh.server~~~~0.0.1.0
if ($cap.State -ne 'Installed') { Add-WindowsCapability -Online -Name openssh.server~~~~0.0.1.0 }
Start-Service sshd
Set-Service sshd -StartupType Automatic

# 2. Dedicated low-privilege backup account (random placeholder password; auth is key-only)
if (-not (Get-LocalUser -Name $BkUser -ErrorAction SilentlyContinue)) {
    $pw = -join ((48..57) + (97..122) | Get-Random -Count 20 | ForEach-Object { [char]$_ })
    New-LocalUser -Name $BkUser -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -PasswordNeverExpires
}

# 3. sshd hardening: public-key auth only
#    (WriteAllLines/WriteAllText = UTF-8 no BOM; PS 5.1 Set-Content writes UTF-16,
#     which OpenSSH chokes on for key files)
$cfg = Join-Path $env:ProgramData 'ssh\sshd_config'
$lines = (Get-Content $cfg) | ForEach-Object {
    if ($_ -match '^#?\s*PasswordAuthentication\s') { 'PasswordAuthentication no' } else { $_ }
}
# dr3backup never logs in interactively -> Windows has no registered profile for it,
# so OpenSSH resolves its home to C:\Windows and looks there for authorized_keys.
# Pin the absolute key path for THIS user only (a shared global file would be a hole).
if ($lines -notmatch 'Match User dr3backup') {
    $lines += @("Match User $BkUser", "`tAuthorizedKeysFile $Profile\.ssh\authorized_keys")
}
[System.IO.File]::WriteAllLines($cfg, $lines)
Restart-Service sshd

# 4. authorized_keys + backup folder (ASCII — OpenSSH rejects UTF-16 key files)
$sshDir = Join-Path $Profile '.ssh'
New-Item -ItemType Directory -Force $sshDir | Out-Null
[System.IO.File]::WriteAllText((Join-Path $sshDir 'authorized_keys'), "$PubKey`n")
icacls $sshDir /grant "NT AUTHORITY\LOCAL SERVICE:(OI)(CI)RX" | Out-Null  # sshd may run as Local Service

# The profile folder was created manually (the account never logs in), so Windows never
# granted dr3backup ownership of its own tree — give it full control explicitly.
icacls $Profile /grant "$BkUser:(OI)(CI)F" | Out-Null

$bk = Join-Path $Profile 'backups\dr3'
New-Item -ItemType Directory -Force $bk | Out-Null

# 5. Daily retention task: keep newest 14 of each (filenames are ISO dates)
$prune = Join-Path $bk 'prune.ps1'
@'
Get-ChildItem "$PSScriptRoot\*.sql.gz" -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -SkipLast 14 | Remove-Item -Force
Get-ChildItem "$PSScriptRoot\*.env"   -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -SkipLast 14 | Remove-Item -Force
'@ | Set-Content $prune
schtasks /Create /F /TN "DR3 Backup Prune" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File $prune" /SC DAILY /ST 04:00 | Out-Null

# 6. Never sleep/hibernate on AC power (leave monitor settings alone)
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

Write-Host ''
Write-Host 'DR3 backup PC setup complete.'
Write-Host "  sshd:            $(Get-Service sshd | ForEach-Object { $_.Status }) (auto-start)"
Write-Host "  backup user:     $BkUser"
Write-Host "  backup folder:   $bk"
Write-Host 'The VPS can now push daily backups here as dr3backup over Tailscale.'
