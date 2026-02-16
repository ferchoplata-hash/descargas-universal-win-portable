$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

if (-not (Test-Path ".venv")) {
  py -3.11 -m venv .venv
}

. .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements-windows.txt pyinstaller

# Instala Chromium dentro del proyecto para poder empacarlo en modo portable.
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $ProjectDir "ms-playwright"
python -m playwright install chromium

$BrowserDir = Join-Path $ProjectDir "ms-playwright"
if (-not (Test-Path $BrowserDir)) {
  throw "No se encontró '$BrowserDir'. La instalación de Playwright no se completó correctamente."
}

pyinstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onedir `
  --name "DescargasUniversalWin" `
  --collect-all playwright `
  --hidden-import playwright.sync_api `
  --add-data "$BrowserDir;ms-playwright" `
  descargas_universal_win.py

$PortableDir = Join-Path $ProjectDir "portable_windows"
if (Test-Path $PortableDir) {
  Remove-Item -Recurse -Force $PortableDir
}
New-Item -ItemType Directory -Path $PortableDir | Out-Null

Copy-Item -Recurse -Force (Join-Path $ProjectDir "dist\DescargasUniversalWin\*") $PortableDir

$Launcher = Join-Path $PortableDir "iniciar_descargas.bat"
@"
@echo off
cd /d %~dp0
start "" "DescargasUniversalWin.exe"
"@ | Set-Content -Encoding ASCII $Launcher

$ZipPath = Join-Path $ProjectDir "DescargasUniversalWin_portable.zip"
if (Test-Path $ZipPath) {
  Remove-Item -Force $ZipPath
}
Compress-Archive -Path (Join-Path $PortableDir "*") -DestinationPath $ZipPath

Write-Host "Build completado."
Write-Host "Carpeta portable: $PortableDir"
Write-Host "ZIP portable:     $ZipPath"
