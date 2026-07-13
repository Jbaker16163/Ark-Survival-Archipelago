@echo off
REM Install the ArkAP plugin (DLL + data files) into your ArkApi Plugins folder.
REM Ships inside ArkAP_plugin.zip - unzip it, then run this from the unzipped folder.
setlocal
set "SRC=%~dp0ArkAP"

if not exist "%SRC%\ArkAP.dll" (
    echo Could not find ArkAP\ArkAP.dll next to this script.
    echo Run this from the unzipped ArkAP_plugin folder ^(it should contain an ArkAP\ subfolder^).
    goto end
)

echo Enter your ArkApi Plugins folder path.
echo Example: E:\ARK\Server\ShooterGame\Binaries\Win64\ArkApi\Plugins
set /p PLUGINS=Path:

if not exist "%PLUGINS%" (
    echo That folder does not exist: %PLUGINS%
    echo Make sure ARK Server API is installed first ^(https://github.com/ServersHub/Framework-ArkServerApi^).
    goto end
)

echo Installing to %PLUGINS%\ArkAP ...
REM /E = include subdirs, merge (does NOT delete ipc/ or tracking files already there).
robocopy "%SRC%" "%PLUGINS%\ArkAP" /E /NFL /NDL /NJH /NJS /NP >nul

echo.
echo Done. The ArkAP plugin is installed. Start (or restart) your ARK dedicated server.
echo Then run the connector on this PC (see the ArkConnector folder).

:end
echo.
pause
