@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1"
if errorlevel 1 (
  echo.
  echo Error en build_windows.ps1
  exit /b 1
)
echo.
echo Listo.
