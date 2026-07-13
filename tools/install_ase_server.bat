@echo off
REM Installs the ARK: Survival Evolved DEDICATED SERVER, pinned to the
REM Pre-Aquatica branch (v358.24) - the mod-compatible build ArkServerApi targets.
REM Also grabs SteamCMD if missing. ~20+ GB download.

setlocal
REM ---- edit these if you want a different location ----
set "SERVER_DIR=H:\Ark archipelago\tools\ASEServer"
set "STEAMCMD_DIR=H:\steamcmd"
REM -----------------------------------------------------

echo Server install dir : %SERVER_DIR%
echo SteamCMD dir       : %STEAMCMD_DIR%
echo Branch             : preaquatica (app 376030)
echo.

REM 1. Get SteamCMD if needed
if not exist "%STEAMCMD_DIR%\steamcmd.exe" (
    echo Downloading SteamCMD...
    if not exist "%STEAMCMD_DIR%" mkdir "%STEAMCMD_DIR%"
    curl -L -o "%STEAMCMD_DIR%\steamcmd.zip" https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip
    if errorlevel 1 ( echo SteamCMD download failed. & goto end )
    powershell -NoProfile -Command "Expand-Archive -Force '%STEAMCMD_DIR%\steamcmd.zip' '%STEAMCMD_DIR%'"
)

REM 2. Install / update the dedicated server on the preaquatica branch
echo.
echo Installing ARK SE dedicated server (preaquatica)... this is large.
"%STEAMCMD_DIR%\steamcmd.exe" +force_install_dir "%SERVER_DIR%" +login anonymous +app_update "376030" -beta preaquatica validate +quit
if errorlevel 1 ( echo Server install failed. & goto end )

echo.
echo Done. Server installed to: %SERVER_DIR%
echo Server exe: %SERVER_DIR%\ShooterGame\Binaries\Win64\ShooterGameServer.exe
echo.
echo NEXT:
echo  - Verify the server version is 358.24 (Pre-Aquatica) and matches your client.
echo  - Then install ArkServerApi into the server's Win64 folder (next step).

:end
endlocal
pause
