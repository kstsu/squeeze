[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$releaseDir = Join-Path $projectRoot "release"
$releaseAppDir = Join-Path $releaseDir "Squeeze"

if (-not (Test-Path $pythonPath)) {
    throw "Create the development environment first with .\run-desktop.ps1 or python -m venv .venv."
}

& $pythonPath -m pip install -r (Join-Path $projectRoot "requirements.txt") pyinstaller==6.22.3 Pillow==12.3.0
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

& $pythonPath (Join-Path $projectRoot "make_icon.py")
if ($LASTEXITCODE -ne 0) { throw "Could not create the Windows icon." }

$pyinstallerArgs = @(
    "--noconfirm",
    "--clean",
    "--onedir",
    "--windowed",
    "--name", "Squeeze",
    "--icon", (Join-Path $projectRoot "squeeze.ico"),
    "--contents-directory", ".",
    "--distpath", $releaseDir,
    "--workpath", (Join-Path $projectRoot "build"),
    "--specpath", (Join-Path $projectRoot "build"),
    "--add-data", "$(Join-Path $projectRoot 'index.html');.",
    "--add-data", "$(Join-Path $projectRoot 'styles.css');.",
    "--add-data", "$(Join-Path $projectRoot 'script.js');.",
    "--add-data", "$(Join-Path $projectRoot 'favicon.svg');.",
    "--collect-binaries", "imageio_ffmpeg",
    "--collect-data", "imageio_ffmpeg",
    "desktop_app.py"
)

& $pythonPath -m PyInstaller @pyinstallerArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$zipPath = Join-Path $releaseDir "Squeeze-windows.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path (Join-Path $releaseAppDir "*") -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host "Desktop build ready at: $releaseAppDir\Squeeze.exe" -ForegroundColor Green
Write-Host "GitHub Release archive ready at: $zipPath" -ForegroundColor Green
