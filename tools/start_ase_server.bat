@echo off
REM Start the ARK: Survival Evolved dedicated server (Pre-Aquatica, ArkApi).
REM Run this AFTER SteamCMD finishes AND ArkApi (version.dll) is installed in Win64.
REM BattlEye is OFF - required for ArkApi.

setlocal
REM ---- edit these ---------------------------------------------------------
set "SERVER_ROOT=E:\ARK\Server"
set "MAP=TheIsland"
set "SESSION=ArchipelagoSolo"
set "MAXPLAYERS=5"
set "GAMEPORT=7777"
set "QUERYPORT=27015"
set "RCONPORT=27020"
set "ADMINPASS=changeme_admin"
set "SERVERPASS="
REM Pseudo-cluster: same ClusterId + ClusterDirOverride on EVERY map's launch = uploads/downloads
REM (via Obelisk/transfer terminal) carry over between maps, even though only one map runs at a
REM time. Leave ClusterId blank to disable clustering entirely.
set "CLUSTERID=GhiosCluster"
set "CLUSTERDIR=E:\ARK\Server\ShooterGame\Saved\ClusterData"
REM ------------------------------------------------------------------------

REM Optional 1st argument overrides MAP (used by switch_map.bat). Double-clicking this file
REM directly still launches the default MAP above, unchanged.
if not "%~1"=="" set "MAP=%~1"

set "EXE=%SERVER_ROOT%\ShooterGame\Binaries\Win64\ShooterGameServer.exe"
if not exist "%EXE%" (
    echo ShooterGameServer.exe not found at:
    echo   %EXE%
    echo Wait for SteamCMD to finish, or fix SERVER_ROOT.
    goto end
)

REM Build the ? option string. ServerPassword line is added only if set.
set "OPTS=%MAP%?listen?SessionName=%SESSION%?Port=%GAMEPORT%?QueryPort=%QUERYPORT%?MaxPlayers=%MAXPLAYERS%?RCONEnabled=True?RCONPort=%RCONPORT%?ServerAdminPassword=%ADMINPASS%"
if not "%SERVERPASS%"=="" set "OPTS=%OPTS%?ServerPassword=%SERVERPASS%"

REM Cluster flags (only if CLUSTERID is set).
set "CLUSTERARGS="
if not "%CLUSTERID%"=="" (
    if not exist "%CLUSTERDIR%" mkdir "%CLUSTERDIR%"
    set "CLUSTERARGS=-ClusterId=%CLUSTERID% -ClusterDirOverride=\"%CLUSTERDIR%\""
)

echo Launching: %SESSION% on %MAP%  (game %GAMEPORT% / query %QUERYPORT% / rcon %RCONPORT%)
if not "%CLUSTERID%"=="" echo Cluster: %CLUSTERID%  (%CLUSTERDIR%)
echo.
"%EXE%" "%OPTS%" -server -log -NoBattlEye %CLUSTERARGS%

:end
endlocal
pause
