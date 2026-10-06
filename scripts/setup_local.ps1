# One-time local setup for Windows (PowerShell). Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_local.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Find a Python 3.11+ (the "py" launcher first, then "python").
$py = $null
foreach ($cand in @(@("py", "-3"), @("python"))) {
    try {
        $ver = & $cand[0] $cand[1..9] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($ver -and [version]$ver -ge [version]"3.11") { $py = $cand; break }
    } catch {}
}
if (-not $py) { throw "Python 3.11 or newer not found. Install it from python.org (tick 'Add python.exe to PATH')." }

if (-not (Test-Path .venv)) { & $py[0] $py[1..9] -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\python.exe -m pip install -e ".[dev,research]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - fill in your keys before running." }
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\tradebot.exe --mode simulated --offline cycle | Out-Null
Write-Host "Offline smoke cycle OK."
Write-Host ""
Write-Host "Next (in this folder):"
Write-Host "  .\.venv\Scripts\Activate.ps1      # then 'tradebot ...' works"
Write-Host "  tradebot research download"
