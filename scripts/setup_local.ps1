# One-time local setup for Windows (PowerShell). Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_local.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Find a Python 3.11+: the "py" launcher first, then "python" on PATH.
$check = "import sys; print('%d.%d' % sys.version_info[:2])"
$usePy = $false
$usePython = $false
try { $v = & py -3 -c $check 2>$null; if ($v -and [version]$v -ge [version]"3.11") { $usePy = $true } } catch {}
if (-not $usePy) {
    try { $v = & python -c $check 2>$null; if ($v -and [version]$v -ge [version]"3.11") { $usePython = $true } } catch {}
}
if (-not ($usePy -or $usePython)) {
    throw "Python 3.11 or newer not found. Install it from python.org (tick 'Add python.exe to PATH')."
}
Write-Host "Using Python $v"

if (-not (Test-Path .venv\Scripts\python.exe)) {
    if ($usePy) { & py -3 -m venv .venv } else { & python -m venv .venv }
}
if (-not (Test-Path .venv\Scripts\python.exe)) { throw "Could not create the .venv virtual environment." }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,research]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed (see the messages above)." }
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - fill in your keys before running." }
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\tradebot.exe --mode simulated --offline cycle | Out-Null
Write-Host "Offline smoke cycle OK."
Write-Host ""
Write-Host "Next (in this folder):"
Write-Host "  .\.venv\Scripts\Activate.ps1      # then 'tradebot ...' works"
Write-Host "  tradebot research download"
