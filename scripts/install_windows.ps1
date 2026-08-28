param(
  [string]$SshHost = "157.90.156.73",
  [string]$SshUser = "senselayer",
  [int]$SshPort = 22,
  [string]$IdentityFile = "$env:USERPROFILE\.ssh\senselayer_ed25519",
  [string]$ExpectedHostPublicKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDibf7clnSPLTVS/1QmpN12KnPl03a1eX3wNYiu34cKh",
  [string]$ExpectedHostKeySha256 = "SHA256:mAUvRHWRLdip9jdSZqsJLdBE/v+yK3PJy1F8wc4Hrh0",
  [string]$MuseName = ""
)

$ErrorActionPreference = "Stop"
$Base = Join-Path $env:LOCALAPPDATA "SenseLayer"
$Venv = Join-Path $Base ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$KnownHosts = Join-Path $Base "known_hosts"
$TaskName = "SenseLayerMuse2"
$Source = $PSScriptRoot
$PipelineBase = Join-Path $Base "pipeline"

function Require-Command([string]$Name, [string]$InstallHint) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "$Name is missing. $InstallHint"
  }
}

Require-Command "ssh.exe" "Install Windows OpenSSH Client in Optional Features."
Require-Command "ssh-keygen.exe" "Install Windows OpenSSH Client in Optional Features."

$PyLauncher = $null
foreach ($candidate in @("py.exe", "python.exe")) {
  if (Get-Command $candidate -ErrorAction SilentlyContinue) { $PyLauncher = $candidate; break }
}
if (-not $PyLauncher) { throw "Python is missing. Install Python 3.11: winget install -e --id Python.Python.3.11" }

New-Item -ItemType Directory -Force -Path $Base | Out-Null
New-Item -ItemType Directory -Force -Path $PipelineBase | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $IdentityFile) | Out-Null

if (-not (Test-Path $IdentityFile)) {
  # Windows PowerShell 5 drops a literal empty-string native argument. Passing
  # two quote characters preserves the required empty passphrase for -N.
  $EmptyPassphraseArgument = '""'
  & ssh-keygen.exe -q -t ed25519 -N $EmptyPassphraseArgument -C "senselayer-windows" -f $IdentityFile
  if ($LASTEXITCODE -ne 0) { throw "SSH key generation failed" }
}

# Pin the already-verified server ED25519 public key directly. This avoids
# ssh-keyscan entirely and makes first-run behavior deterministic on Windows.
$KnownHostName = if ($SshPort -eq 22) { $SshHost } else { "[$SshHost]:$SshPort" }
"$KnownHostName $ExpectedHostPublicKey" | Set-Content -Encoding ascii $KnownHosts
$FingerprintLine = & ssh-keygen.exe -lf $KnownHosts -E sha256
if ($LASTEXITCODE -ne 0 -or $FingerprintLine -notmatch [regex]::Escape($ExpectedHostKeySha256)) {
  throw "Bundled SSH host key validation failed. Expected $ExpectedHostKeySha256; got: $FingerprintLine"
}

if (-not (Test-Path $Python)) {
  & $PyLauncher -m venv $Venv
  if ($LASTEXITCODE -ne 0) { throw "Python virtual environment creation failed" }
}
& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
& $Python -m pip install -r (Join-Path $Source "requirements-edge.txt")
if ($LASTEXITCODE -ne 0) { throw "SenseLayer Python dependency installation failed" }

Copy-Item -Force (Join-Path $Source "muse2_edge_collector.py") (Join-Path $Base "muse2_edge_collector.py")
Copy-Item -Force (Join-Path $Source "senselayer_supervisor.py") (Join-Path $Base "senselayer_supervisor.py")
Copy-Item -Force (Join-Path $Source "..\pipeline\__init__.py") (Join-Path $PipelineBase "__init__.py")
Copy-Item -Force (Join-Path $Source "..\pipeline\eeg_quality.py") (Join-Path $PipelineBase "eeg_quality.py")

$Config = [ordered]@{
  ssh_host = $SshHost
  ssh_user = $SshUser
  ssh_port = $SshPort
  identity_file = $IdentityFile
  known_hosts_file = $KnownHosts
  local_receiver_port = 18787
  local_dashboard_port = 18501
  muse_name = $MuseName
  muse_start_delay = 7
  source_name = "muse2-edge-win11"
  post_interval = 1.0
  simulate = $false
}
$Config | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Base "config.json")

$PreviousErrorActionPreference = $ErrorActionPreference
try {
  # A not-yet-authorized key is expected on the first run. Capture the native
  # exit code instead of letting PowerShell abort on SSH's stderr message.
  $ErrorActionPreference = "Continue"
  & ssh.exe -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KnownHosts" -o IdentitiesOnly=yes -i $IdentityFile -p $SshPort "$SshUser@$SshHost" true 2>$null
  $SshExitCode = $LASTEXITCODE
} finally {
  $ErrorActionPreference = $PreviousErrorActionPreference
}
if ($SshExitCode -ne 0) {
  Write-Host "The dedicated SSH key is not authorized yet. Send this public key to the dn-dev-01 administrator:" -ForegroundColor Yellow
  Get-Content "$IdentityFile.pub"
  throw "SSH authorization missing. No autostart task was created. Authorize the key, then run this installer again."
}

$Runner = Join-Path $Base "run_senselayer.cmd"
@"
@echo off
"$Python" "$Base\senselayer_supervisor.py" --config "$Base\config.json" >> "$Base\senselayer.log" 2>&1
"@ | Set-Content -Encoding ascii $Runner

$QuotedRunner = '"' + $Runner + '"'
schtasks.exe /Create /SC ONLOGON /TN $TaskName /TR $QuotedRunner /RL LIMITED /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Task Scheduler registration failed" }

Write-Host "SenseLayer installed idempotently." -ForegroundColor Green
Write-Host "Autostart task: $TaskName"
Write-Host "Dashboard after startup: http://127.0.0.1:18501"
Write-Host "Manual start: schtasks /Run /TN $TaskName"
