# Start the Arbiter web UI: API on 8000, Vite on 5173.
#   powershell -File scripts\ui_up.ps1
#
# Assumes the stack is already running (scripts\hydradb_up.ps1) and a graph has
# been ingested. Both processes run in their own windows so either can be
# restarted without killing the other.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) { Write-Host 'run: python -m venv .venv' -ForegroundColor Red; exit 1 }
if (-not (Test-Path (Join-Path $root 'ui\node_modules'))) {
    Write-Host 'installing UI dependencies...' -ForegroundColor Yellow
    Push-Location (Join-Path $root 'ui'); npm install; Pop-Location
}

Start-Process -FilePath $python -ArgumentList '-m', 'uvicorn', 'api:app', '--port', '8000' `
    -WorkingDirectory $root -WindowStyle Minimized

$ready = $false
foreach ($i in 1..20) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 3
        if ($r.ok) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    Write-Host 'API did not come up, or the graph is empty. Try: python -m ingest.load' -ForegroundColor Red
}

Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'npm', 'run', 'dev' `
    -WorkingDirectory (Join-Path $root 'ui') -WindowStyle Minimized

Start-Sleep -Seconds 4
Write-Host 'Arbiter UI  ->  http://127.0.0.1:5173' -ForegroundColor Green
Write-Host 'API         ->  http://127.0.0.1:8000/api/health' -ForegroundColor DarkGray
Start-Process 'http://127.0.0.1:5173'
