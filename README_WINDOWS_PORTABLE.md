# Descargas Universal - Build portable para Windows

Esta carpeta es **independiente** del proyecto original.

## Contenido
- `descargas_universal_win.py`: copia adaptada para empaquetado Windows.
- `requirements-windows.txt`: dependencias base.
- `build_windows.ps1`: script principal de build portable.
- `build_windows.bat`: lanzador del script PowerShell.

## Importante
- Un `.exe` de Windows **no se puede compilar nativamente desde macOS** con PyInstaller.
- Debes ejecutar el build en una PC con Windows (o VM Windows).

## Requisitos en Windows
- Windows 10/11 (64-bit)
- Python 3.11 instalado y disponible como `py`
- PowerShell habilitado

## Cómo generar el `.exe` portable
1. Copia esta carpeta a una máquina Windows.
2. Abre CMD o PowerShell dentro de la carpeta.
3. Ejecuta:
   - `build_windows.bat`

El script hace todo:
- crea `.venv`
- instala dependencias
- descarga Chromium de Playwright dentro del proyecto
- genera el ejecutable con PyInstaller
- crea carpeta portable en `portable_windows`
- genera ZIP portable: `DescargasUniversalWin_portable.zip`

## Resultado
- Ejecutable final: `portable_windows\\DescargasUniversalWin.exe`
- Lanzador opcional: `portable_windows\\iniciar_descargas.bat`
- Paquete para compartir: `DescargasUniversalWin_portable.zip`

## Nota técnica
Se embebe la carpeta `ms-playwright` dentro del paquete para que el `.exe` funcione sin instalar Playwright aparte.

## Build en la nube con GitHub Actions
También quedó configurado un workflow:
- `.github/workflows/build-windows-portable.yml`

Pasos:
1. Sube esta carpeta a un repositorio GitHub.
2. En GitHub, ve a `Actions` -> `Build Windows Portable`.
3. Pulsa `Run workflow`.
4. Cuando termine, descarga los artifacts:
   - `DescargasUniversalWin_portable_folder`
   - `DescargasUniversalWin_portable_zip`

El ZIP trae el paquete portable listo para usar en Windows.
