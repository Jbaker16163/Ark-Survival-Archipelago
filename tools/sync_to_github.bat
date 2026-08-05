@echo off
REM Sync the public-source folders from the working repo into the GitHub-connected folder.
REM Mirrors (adds/updates/deletes) apworld/, data/, tools/, connector/, plugin/, poptracker/,
REM docs/, BUGS.md, README.md - the "full scope" source set - while excluding build junk,
REM caches, the abandoned UE4SS downloads, and the live (deployed) ArkAP.config.json.
REM
REM Does NOT touch .git/ or .gitignore in the destination, and does NOT commit or push -
REM review with `git status` / `git diff` in the destination folder, then commit + push yourself.

setlocal
REM ---- edit these if your paths differ ----
set "SRC=H:\Ark archipelago"
set "DST=B:\ARK Github\Ark-Survival-Archipelago"
REM ------------------------------------------

if not exist "%SRC%" (
    echo Source not found: %SRC%
    goto end
)
if not exist "%DST%\.git" (
    echo Destination doesn't look like a git repo ^(no .git found^): %DST%
    echo Refusing to sync - check DST above.
    goto end
)

set "RCOPTS=/NFL /NDL /NJH /NJS /NP"

echo Syncing apworld...
robocopy "%SRC%\apworld" "%DST%\apworld" /MIR /XD __pycache__ %RCOPTS%

echo Syncing data...
robocopy "%SRC%\data" "%DST%\data" /MIR %RCOPTS%

echo Syncing tools...
robocopy "%SRC%\tools" "%DST%\tools" /MIR /XD downloads __pycache__ /XF download_ue4ss.bat sync_to_github.bat %RCOPTS%

echo Syncing connector...
robocopy "%SRC%\connector" "%DST%\connector" /MIR /XD __pycache__ build dist %RCOPTS%

echo Syncing plugin...
robocopy "%SRC%\plugin" "%DST%\plugin" /MIR /XD .vs x64 "%SRC%\plugin\ArkAP\ArkAP" /XF *.user UpgradeLog.htm ArkAP.config.json %RCOPTS%

echo Syncing poptracker...
robocopy "%SRC%\poptracker" "%DST%\poptracker" /MIR %RCOPTS%

echo Syncing docs...
robocopy "%SRC%\docs" "%DST%\docs" /MIR %RCOPTS%

echo Syncing root files...
copy /Y "%SRC%\BUGS.md" "%DST%\BUGS.md" >nul
copy /Y "%SRC%\README.md" "%DST%\README.md" >nul

echo.
echo Done. Review changes before publishing:
echo   cd "%DST%"
echo   git status
echo   git add -A ^&^& git commit -m "..." ^&^& git push
echo.

:end
endlocal
pause
