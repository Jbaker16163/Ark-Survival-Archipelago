@echo off
REM Run the ARK <-> Archipelago connector using settings from connector.ini (in this folder).
REM Edit connector.ini first (server, slot, ipc_dir). Needs Python 3 + websockets, OR use the
REM prebuilt ArkConnector.exe from the release (which needs neither).
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python not found on PATH.
    echo Install Python 3 from https://www.python.org/  ^(or use ArkConnector.exe from the release^).
    goto end
)

REM ensure the websockets dependency is present
python -c "import websockets" 2>nul
if errorlevel 1 (
    echo Installing dependency: websockets ...
    python -m pip install --quiet websockets
)

echo Starting connector ^(reads connector.ini^)...
python ark_ap_connector.py %*

:end
echo.
pause
