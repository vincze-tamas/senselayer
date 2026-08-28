$ErrorActionPreference = "Stop"
$TaskName = "SenseLayerMuse2"
$Base = Join-Path $env:LOCALAPPDATA "SenseLayer"

schtasks.exe /End /TN $TaskName 2>$null | Out-Null
schtasks.exe /Delete /TN $TaskName /F 2>$null | Out-Null
Write-Host "Autostart removed. Runtime files remain at $Base for safe rollback."
Write-Host "Delete that directory manually only if the rollback period is over."
