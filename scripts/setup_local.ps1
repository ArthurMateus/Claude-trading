# One-time local setup for Windows (PowerShell). Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_local.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path .venv)) { py -3.11 -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - fill in your keys before running." }
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\tradebot.exe --mode simulated --offline cycle | Out-Null
Write-Host "Offline smoke cycle OK."
Write-Host "Next: edit .env, then: .\.venv\Scripts\tradebot.exe validate ; .\.venv\Scripts\tradebot.exe cycle ; scripts\run_local.ps1"
