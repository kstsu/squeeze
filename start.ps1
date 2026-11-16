[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $pythonPath)) {
    Write-Host "No project virtual environment was found. Creating .venv..." -ForegroundColor Yellow
    python -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Python could not create .venv. Close any process using .venv and try again."
    }
}

$dependencies = & $pythonPath -c "import fastapi, imageio_ffmpeg, uvicorn" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing project dependencies..." -ForegroundColor Yellow
    & $pythonPath -m pip install -r (Join-Path $projectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed."
    }
}

$listener = Get-NetTCPConnection -LocalAddress $HostAddress -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    $ownerName = if ($owner) { $owner.ProcessName } else { "unknown process" }
    Write-Host "Port $Port is already in use by $ownerName (PID $($listener.OwningProcess))." -ForegroundColor Yellow
    Write-Host "If this is Squeeze, open http://$HostAddress`:$Port" -ForegroundColor Cyan
    Write-Host "To use a different port, run: .\start.ps1 -Port 8001" -ForegroundColor Cyan
    Write-Host "To inspect the owner, run: Get-Process -Id $($listener.OwningProcess)" -ForegroundColor DarkGray
    exit 1
}

Write-Host "Starting Squeeze at http://$HostAddress`:$Port" -ForegroundColor Green
& $pythonPath -m uvicorn app:app --host $HostAddress --port $Port
