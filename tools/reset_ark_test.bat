@echo off
REM Reset the ARK world save + all ArkAP/connector tracking for a clean test run.
REM Run on the SERVER PC. The save is MOVED to a timestamped backup (not deleted).
REM !! STOP the ARK dedicated server first (save files are locked while it runs) !!

setlocal
REM ---- edit if your paths differ ----
set "SERVER_ROOT=E:\ARK\Server"
REM -----------------------------------
set "PLUGIN=%SERVER_ROOT%\ShooterGame\Binaries\Win64\ArkApi\Plugins\ArkAP"
set "SAVED=%SERVER_ROOT%\ShooterGame\Saved\SavedArks"

echo This will:
echo   - back up + clear the ARK world save:  %SAVED%
echo   - clear ArkAP + connector tracking in: %PLUGIN%
echo.
echo Make sure the ARK server is STOPPED.
pause

REM verify server isn't running (exe locked)
tasklist /fi "imagename eq ShooterGameServer.exe" | find /i "ShooterGameServer.exe" >nul
if not errorlevel 1 (
    echo.
    echo ShooterGameServer.exe is STILL RUNNING. Stop it first, then re-run.
    goto end
)

REM timestamped backup of the world save
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "TS=%%i"
set "BACKUP=%SERVER_ROOT%\ShooterGame\Saved\SavedArks_backup_%TS%"
if exist "%SAVED%" (
    echo Backing up save -^> %BACKUP%
    move "%SAVED%" "%BACKUP%" >nul
)
mkdir "%SAVED%" 2>nul

REM clear plugin + connector tracking (regenerated on next run)
echo Clearing tracking files...
del /q "%PLUGIN%\state.json"            2>nul
del /q "%PLUGIN%\seed.json"             2>nul
del /q "%PLUGIN%\ipc\state.json"        2>nul
del /q "%PLUGIN%\ipc\checks_out.jsonl"  2>nul
del /q "%PLUGIN%\ipc\items_in.jsonl"    2>nul
del /q "%PLUGIN%\ipc\death_out.jsonl"   2>nul
del /q "%PLUGIN%\ipc\death_in.jsonl"    2>nul
del /q "%PLUGIN%\ipc\msg_in.jsonl"      2>nul
del /q "%PLUGIN%\ipc\hint_out.jsonl"    2>nul
del /q "%PLUGIN%\ipc\hint_status.json"  2>nul
del /q "%PLUGIN%\ipc\flags.json"        2>nul
del /q "%PLUGIN%\applied_index.json"    2>nul
del /q "%PLUGIN%\counters.json"         2>nul
del /q "%PLUGIN%\events_queue.jsonl"    2>nul
del /q "%PLUGIN%\ArkAP_note_hits.jsonl" 2>nul
del /q "%PLUGIN%\note_queue.jsonl"      2>nul
del /q "%PLUGIN%\tame_check_queue.jsonl" 2>nul
del /q "%PLUGIN%\kill_check_queue.jsonl" 2>nul
del /q "%PLUGIN%\dino_queue.jsonl"      2>nul
del /q "%PLUGIN%\crate_queue.jsonl"     2>nul
del /q "%PLUGIN%\ArkAP_debug.log"       2>nul
REM multiplayer: each player's mailbox is an ipc\<CharacterName> subfolder - wipe them all.
for /d %%D in ("%PLUGIN%\ipc\*") do rd /s /q "%%D" 2>nul

echo.
echo Done. World save backed up to:
echo   %BACKUP%
echo Start the server for a fresh world. (Reset the AP multiworld separately by
echo re-hosting / regenerating the .archipelago on the host PC.)

:end
endlocal
pause
