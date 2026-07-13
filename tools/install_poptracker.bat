@echo off
REM Regenerate the PopTracker pack from the data files and install it into PopTracker's packs
REM folder, so you can load + test it immediately. Also drops a shareable zip in dist\.
setlocal
set "REPO=%~dp0.."

REM ---- edit if your PopTracker packs folder is elsewhere ----
set "POPTRACKER_PACKS=C:\Users\justi\Downloads\poptracker\packs"
REM ----------------------------------------------------------
set "PACKNAME=ark_survival_evolved_ap"

echo(
echo [1/3] Regenerating pack from data files...
python "%REPO%\tools\gen_poptracker.py"
if errorlevel 1 (
    echo   ERROR: pack generation failed ^(is python on PATH?^).
    goto end
)

echo(
echo [2/3] Installing pack -^> %POPTRACKER_PACKS%\%PACKNAME%
if not exist "%POPTRACKER_PACKS%" mkdir "%POPTRACKER_PACKS%"
REM /MIR mirrors the folder (removes stale files). robocopy returns 0-7 on success, so don't gate on it.
robocopy "%REPO%\poptracker" "%POPTRACKER_PACKS%\%PACKNAME%" /MIR /NFL /NDL /NJH /NJS /NP >nul

echo(
echo [3/3] Building shareable zip -^> dist\%PACKNAME%.zip
if not exist "%REPO%\dist" mkdir "%REPO%\dist"
powershell -NoProfile -Command "Compress-Archive -Path '%REPO%\poptracker\*' -DestinationPath '%REPO%\dist\%PACKNAME%.zip' -Force"

echo(
echo Done.
echo   Installed to: %POPTRACKER_PACKS%\%PACKNAME%
echo   Zip (for friends): %REPO%\dist\%PACKNAME%.zip
echo(
echo Open PopTracker, load "ARK: Survival Evolved (Archipelago)", then connect on the AP tab.
echo (If PopTracker doesn't see it, check its packs folder path and edit POPTRACKER_PACKS at the
echo  top of this bat.)

:end
echo(
pause
