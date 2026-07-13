@echo off
REM Build a standalone ArkConnector.exe (no Python needed by end users) with PyInstaller.
REM One-time on YOUR machine: `pip install pyinstaller websockets`, then run this.
REM Output: dist\ArkConnector.exe  (bundle it in the GitHub release alongside connector.ini).
setlocal
cd /d "%~dp0"

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    python -m pip install pyinstaller websockets
)

echo Building ArkConnector.exe ...
python -m PyInstaller --onefile --name ArkConnector --console ark_ap_connector.py

echo.
echo Done. Exe at: %~dp0dist\ArkConnector.exe
echo Ship it next to connector.ini - the exe reads connector.ini from its own folder.
echo.
pause
