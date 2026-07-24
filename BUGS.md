# Known bugs / open items

## Added 2026-07-23 (apworld/data): cave_tames corrected - PASSIVE tames were gated as KO tames

- Review by Lurch9229 (2026-07-23): several `cave_tames` entries are PASSIVE tames, so requiring a
  tranq weapon was simply the wrong mechanic. It was wrong in BOTH directions - over-gating (you
  needed the whole Anvil+Forge+Crossbow chain to tame a Dung Beetle) and under-gating (we never
  asked for Ghillie / Bug Repellent, which is the ACTUAL barrier for a passive approach).
- Now:
  * PASSIVE -> `Ghillie | Bug Repellent`: Dung Beetle, Araneo, Onyc, Arthropleura.
    Compiles to a real either/or with NO tranq weapon in it - Ghillie branch = Ghillie Shirt +
    Smithy; Bug Repellent branch = Bug Repel + Narcotic + Mortar And Pestle + a crop plot.
    (Arthropleura also lost its `+ Gas Mask`: it's obtainable in the open swamp/redwoods, the gas
    is only in the Swamp CAVE.)
  * KO (reviewer raised no objection) unchanged: Pulmonoscorpius, Megalania -> Crossbow KO
    (auto-relaxed to `Bow KO | Crossbow KO`), Megalosaurus -> Rifle KO.
  * Titanoboa REMOVED from cave_tames and added to `NO_TAME_LOGIC`. It's a passive tame needing a
    FERTILIZED EGG (breeding - not modelled, same reason breed-count milestones are filler-only)
    and is easier to find in the open swamp than a cave. NOTE: deleting it from cave_tames alone
    was NOT enough - it would have fallen back to the SHEET, which also says `Bow KO | Crossbow KO`.
- Verified: 3-seed gen beatable, no accessibility failures; `Tamed: Titanoboa` holds filler only and
  never appears in a playthrough.

## Added 2026-07-23 (plugin): v96 - "host:port" rendered as an emoji in ARK chat

- ARK's chat runs an emoticon substitution, so the ":p" inside `<host:port>` came out as a tongue
  emoji: "use /connect <slot> <host(emoji)ort>". Cosmetic but it makes the usage text unreadable -
  and unusable, since players copy it.
- Fixed every PLAYER-FACING occurrence to `<host>:<port>` (colon is followed by "<", which no
  emoticon matches): the /connect usage line, the "couldn't find a host and port" error, the
  join-greeting fallback, "bad server address", and the /apstatus empty-list line. Comments in the
  source still say host:port - they never reach chat.
- Also corrected the stale argument order in the /apstatus line: it said `<slot> <host:port>`,
  but the documented order is `<host>:<port> <slot>` (both are still accepted by ApParseServer).
- Verified the shipped dll contains no "host:port" in either narrow or wide string form.
- WATCH OUT when writing chat text: any ":" immediately followed by a letter risks the same
  substitution (":p", ":d", ":o" ...). The concrete example "archipelago.gg:38281" is safe (digit).

## Added 2026-07-23 (plugin): v95 - greeting needed a delay before the client can show chat

- v94 DETECTION is confirmed correct by the user's log: every LOGOUT is followed by a GREET on
  rejoin (22:02:49->22:03:11, 22:04:07->22:04:22, 22:04:32->22:04:46, 22:05:22->22:05:35) and the
  two deaths at 22:03:03 / 22:03:23 produced NO greet. Both hook and NetConnection poll work.
- Remaining problem was purely DELIVERY: the log said GREET but the player saw nothing in chat.
  Having a `GetPlayerCharacter()` is NOT the same as the client being ready to display chat - the
  message is accepted server-side and silently never rendered. (This is the same class of bug as
  the swallowed /confirm prompt: sending before the receiver exists.)
- Fix: after the character appears, wait `GREET_DELAY_SEC = 8` (tracked per controller in
  `g_greetDue`) and send then. Entries are pruned if the player leaves first, and the Logout hook
  clears both maps. Log line is now "GREET (sent to client)" so the log reflects the actual send.

## Added 2026-07-23 (plugin): v94 - detect LEAVE, not join, for the rejoin greeting

- v93 (controller-pointer identity) still missed rejoins. Two independent reasons, both fatal:
  1. a disconnected controller LINGERS in PlayerControllerList, so it never left the "greeted" set;
  2. ARK can REUSE the same controller object on reconnect, so even correct pruning wouldn't have
     produced a new identity.
- Detecting LEAVE is far more reliable than detecting join (the join hook
  `HandleNewPlayer_Implementation` never fired at all - v91). Two independent mechanisms now:
  * HOOK `AShooterGameMode.Logout` -> `ForgetGreeted(Exiting)` clears that controller immediately,
    and logs "LOGOUT -> greet state cleared" so we can SEE whether it fires.
  * POLL: a controller is only counted present if `pc->NetConnectionField()` is non-null. A
    lingering post-logout controller has none, so it drops out of the set on the next tick even if
    the hook never fires. Works for controller reuse too (the gap is observed in between).
- Only pointers are compared, never dereferenced, so a stale controller is harmless.

## Added 2026-07-23 (plugin): v93 - rejoin greeting fixed (controller-pointer identity)

- v91's login hook `AShooterGameMode.HandleNewPlayer_Implementation` NEVER FIRED: the user's log has
  no `GREET queued for` line at all, so v91 was worse than v90 (which at least greeted on first
  join). Hook removed - don't re-try it without first proving it fires.
- Correct identity for "is this a new join?" is the PlayerController POINTER:
    * REJOIN  -> brand-new controller  -> unseen pointer -> greet
    * RESPAWN -> SAME controller reused (only the character is swapped) -> already greeted, silent
  Keying on the survivor NAME (v90) failed both ways: a logged-out controller lingers in the list
  (name never cleared -> rejoins silent) and death briefly nulls the character (name dropped ->
  respawn falsely greeted). Pointers are only ever COMPARED against the live controller list, never
  dereferenced, so a stale one is harmless.
- Greeting is deferred until `GetPlayerCharacter()` exists, so it isn't sent mid-load and lost.
- REMOVED `/destroywilddinos` (user request). Its own log line proved the point: v91 reported
  "native cheat-manager call" - i.e. the cheat manager WAS present and the native call still didn't
  visibly wipe - so the command was never going to be reliable. `-ForceRespawnDinos` on the
  /confirm restart is the mechanism that actually works. `WipeWildNow` deleted with it.

## Added 2026-07-23 (plugin): v92 - -ForceRespawnDinos confirmed; in-game wipe now admin-gated

- User confirmed: `-ForceRespawnDinos` on the /confirm restart WORKS; `/destroywilddinos` did not.
  That settles the diagnosis - a normal player's `CheatManagerField()` is NULL, so the cheat call
  had nothing to run on (and the console string silently no-ops for a non-admin).
- `/destroywilddinos` fixed: if there's no cheat manager and the caller IS an admin
  (`pc->bIsAdmin()()`), `AddCheats(true)` instantiates one and the native DestroyWildDinos runs.
  Deliberately admin-ONLY - handing a cheat manager to any player who types a command would be a
  privilege escalation; an admin can already run the cheat, so nothing extra is granted. Non-admins
  now get an honest failure telling them to enablecheats or use /confirm.
  (BitFieldValue -> bool needs `operator()()`: `pc->bIsAdmin()()`.)
- REMOVED the marker-based automatic wipe (`ap_wipe_wild.flag`, DoWipeWildDinos, its tick call).
  -ForceRespawnDinos already does it at boot, and the old path would log/announce "wild creatures
  cleared" while actually doing nothing for a non-admin - a lie in the log. /confirm's chat line now
  names the real mechanism.

## Added 2026-07-23 (plugin): greeting moved to the real LOGIN hook (v91) - v90 polling was wrong

- v90 symptoms: greeting appeared on FIRST join, never on a rejoin, and (wrongly) fired again on
  death+respawn.
- Cause: v90 inferred "joined" by polling for a controller that has a character.
  Both directions were wrong - a logged-out controller LINGERS in PlayerControllerList (so the name
  never left the greeted set and a rejoin never re-triggered), while DEATH briefly nulls the
  character (so the name dropped out and respawning looked like a new join).
- Fix: hook `AShooterGameMode.HandleNewPlayer_Implementation` and greet only when `bIsFromLogin` is
  true - that flag is exactly the login-vs-respawn distinction polling can't see.
- Chat is NOT sent inside the hook: the client isn't ready for chat at that instant and the message
  would be lost (same failure mode as the swallowed /confirm prompt). The greet is queued by
  survivor NAME (a controller pointer can dangle if they drop), re-resolved on the game tick ~3s
  later, retried while they're still loading, and abandoned after 30s.

## Added 2026-07-23 (plugin): per-player AP status greeting on JOIN (v90)

- Report: "when I log back in I don't see a message that I'm connected - I thought we implemented
  this." Half-implemented: `DoShowConnStatus` announces on a status CHANGE (conn_status seq) and
  then records that seq for the whole SERVER session. A player logging out and back in while
  nothing changed was therefore never told anything - the earlier FirstReadyPlayer() guard only
  fixed the "nobody online when it first fired" case, not rejoins.
- Fix: new `GreetJoiners()` tick. Tracks greeted survivors by character name; on a newly-ready
  player it reads THAT player's mailbox conn_status.txt and sends the current state to them alone
  (`SendChatMessage`, not broadcast), falling back to "AP: not connected - use /connect ...".
  Names are dropped from the set on logout so a rejoin greets again.
- Gotcha: `ApiUtils::SendChatMessage` runs the text through `FString::Format` (fmt-style), so the
  status is passed as an ARGUMENT with a `L"{}"` format string - a brace in a survivor name would
  otherwise be parsed as a format field.
- Build marker -> `v90-join-greeting`. (A transient LNK1000 needed a /t:Rebuild; verified the final
  dll contains v90-join-greeting, GREET, /destroywilddinos and ARKAP_FORCE_RESPAWN.)

## Added 2026-07-23 (plugin): /destroywilddinos manual command + build marker bumped to v89

- User's log still showed the OLD line `WIPE: DestroyWildDinos executed after /confirm restart`,
  which the cheat-manager rework had already replaced -> that server was running a STALE dll. The
  build marker had not been bumped after v88, so there was no way to tell builds apart. Marker is
  now `v89-forcerespawn-destroywild`; ALWAYS bump it when shipping a plugin change.
- Added `/destroywilddinos` chat command so the wipe can be tested on demand, independently of the
  post-restart timing. Both it and the automatic wipe now call a shared `WipeWildNow(pc)` that
  RETURNS which path ran, and that string goes to chat + ArkAP_debug.log:
  "native cheat-manager call" or "no cheat manager; console fallback: ['cheat DestroyWildDinos' ->
  '<result>'] [...]". That distinguishes "cheat manager is null" from "command ran but did nothing".
- Verified the shipped dll actually contains the new symbols (v89 marker, /destroywilddinos,
  ARKAP_FORCE_RESPAWN, -ForceRespawnDinos, and the wide-string console fallbacks).

## Added 2026-07-23 (plugin/tools): wild-dino wipe now uses -ForceRespawnDinos (DestroyWildDinos no-op)

- Report: the log said "WIPE: DestroyWildDinos executed" but nothing was actually destroyed.
- Cause: `DestroyWildDinos` is a CHEAT command. Passing the bare string to `pc->ConsoleCommand`
  isn't routed to the cheat manager for a non-admin controller, so it silently does nothing - and
  `APlayerController::CheatManagerField()` is typically NULL for a normal player, so calling the
  native `UShooterCheatManager::DestroyWildDinos()` can't be relied on either.
- PRIMARY fix: ARK's own startup flag `-ForceRespawnDinos`, applied for ONE boot by the /confirm
  relauncher. No admin rights, no player needed, no cheat routing.
  - launcher path: relauncher sets `ARKAP_FORCE_RESPAWN=1`; start_ase_server.bat turns that into
    `-ForceRespawnDinos` (verified: env var survives `call` into the script's `setlocal`, flag
    appears ONLY on the relaunch, a normal manual start is unaffected).
  - cmdline-replay path: the flag is appended to the replayed command line directly.
- Kept the in-game attempt as a fallback for hosts whose start_ase_server.bat predates this: it now
  tries the native cheat-manager call first and falls back to `cheat DestroyWildDinos` /
  `DestroyWildDinos`, logging the result string of each so a failure is diagnosable.
- ACTION FOR EXISTING HOSTS: start_ase_server.bat is customised per host (SERVER_ROOT etc), so an
  existing copy must have the FORCERESPAWN block + `%FORCERESPAWN%` on the launch line merged in,
  otherwise only the unreliable in-game path runs.

## Added 2026-07-23 (tools): reset_ark_test.bat now strips randomize_dino_spawns from Game.ini

- Without it the previous seed's biome rosters stay live on a "fresh" world, and /confirm sees them
  as already applied so it never re-prompts.
- Can't just cut our `; === ArkAP ... BEGIN/END ===` block: ARK rewrites Game.ini and STRIPS
  COMMENTS, so after a restart the Config lines survive with no markers around them. The filter
  matches the LINES: `ConfigOverrideNPCSpawnEntriesContainer` / `ConfigAddNPCSpawnEntriesContainer`
  / `NPCReplacements` (plus any leftover ArkAP marker comments). Everything else is preserved and a
  timestamped `Game.ini.apbak_<TS>` copy is written first.
- Batch-quoting gotcha: inside a double-quoted string cmd treats `^` as LITERAL, so the usual `^|`
  escape would have passed a stray caret to PowerShell. Used `$l.Where({...})` so the command
  contains no pipe at all.
- Verified by running the exact batch block: removed 5 lines (both markers, the in-block line, a
  marker-less line, and NPCReplacements), kept BabyMatureSpeed/HarvestAmount/section headers,
  wrote the .apbak backup, and produced no UTF-8 BOM.

## Added 2026-07-23 (plugin): /confirm re-prompted forever after a successful apply

- Spotted in the user's debug log: the whole chain finally worked (prompt -> /confirm -> patch ->
  relauncher -> server back in 22s -> WIPE waited for a player then ran DestroyWildDinos), but then
  `PROMPT: randomized spawns pending` fired AGAIN at 17:44:32, after the settings were already live.
- Cause: "already applied" was detected by looking for our `; === ArkAP ... BEGIN/END ===` comment
  markers. ARK rewrites Game.ini and STRIPS COMMENTS, so after the restart the markers are gone even
  though the ConfigOverride lines survive -> the check said "pending" -> prompt again, and a second
  /confirm would have restarted the server again. Infinite loop.
- Fix: decide on the Config LINES, not the markers. `HasIniLine()` does whole-line matching (handles
  ARK reordering them); if every fragment line is present -> return 0 (applied). When a re-apply IS
  needed, `RemoveIniLine()` first strips any stray copies so the block can't duplicate lines.
  Logic unit-tested for: fresh ini, right-after-patch, ARK-stripped-markers (the observed case),
  and partial-loss + dedupe.
- Also bumped ARKAP_BUILD to `v88-confirm-restart-wipe` (was still `v87-boss-goal`, so
  ArkAP_loaded.txt / the LOAD line never changed across all these fixes - user noticed).

## Added 2026-07-23 (plugin): ROOT-CAUSED - one-shot announcements lost on an auto-resumed start

- Two more reports, same underlying bug as the swallowed /confirm prompt: (1) DestroyWildDinos
  didn't run after the restart and no message appeared; (2) the AP connect message never appeared
  on rejoin ("I was most likely connected while offline").
- Pattern: the server comes up, auto-resumes the AP session and fires a ONE-SHOT announcement while
  NOBODY is in-world. The "already handled" state advances anyway, so the player who joins seconds
  later never sees it. `DoShowConnStatus` recorded `shown[route] = seq` and ChatNotify'd with zero
  players online; the wipe only checked for a PlayerController, which can exist while the player is
  still loading (no character yet -> chat/console command go nowhere).
- Fix: shared `FirstReadyPlayer()` - returns a controller only if it has a spawned character.
  Every one-shot now gates on it BEFORE advancing its state: conn-status (returns without consuming
  the seq), the /confirm prompt, and the wild-dino wipe (keeps the marker and retries, logging
  every ~30 ticks while it waits).
- NOT changed: `DoApplyMessages`'s boot-swallow. That one deliberately drops backlog so a joining
  player isn't flooded with old item-flow chatter; only the durable one-shots needed the guard.

## Added 2026-07-23 (plugin): /confirm now wipes wild dinos after the restart

- User request. Changing the spawn containers in Game.ini only affects NEW spawns - the creatures
  already in the world save persist, so a randomized map looks unchanged until they slowly despawn.
- `/confirm` now writes a marker (`ap_wipe_wild.flag`, next to the dll so it survives the hard
  exit) before restarting; after the server is back up, the tick runs `DestroyWildDinos` once and
  deletes the marker. NOTE: the real ARK command is `DestroyWildDinos` - there is no
  "killwilddinos".
- It runs via `pc->ConsoleCommand` (same idiom as the trap SpawnDino code) because DestroyWildDinos
  lives on the per-player UShooterCheatManager - there's no PC-free variant in the SDK. So the
  marker is only consumed once a controller actually exists; until then it retries each tick rather
  than burning the one-shot on an empty server (same trap as the swallowed prompt).
- reset_ark_test.bat also clears the marker.

## Added 2026-07-23 (plugin): RESOLVED - the /confirm prompt was eaten by the msg_in boot-swallow

- Report: restart via /confirm now works, but the prompt telling you to type /confirm NEVER shows
  (confirmed on a different PC with a different yaml that DOES enable randomize_dino_spawns).
- Root cause: the prompt was sent with `QueueMsg` -> `msg_in.jsonl`. `DoApplyMessages` deliberately
  swallows everything present on the FIRST tick for a route ("don't replay old chat history",
  PluginMain.cpp ~1631). On a server start the resumed AP session writes the fragment + prompt
  BEFORE that first tick, so the prompt was marked as already-seen every time. (The connect/
  disconnect lines survive only because conn_status.txt uses a separate seq-based path - its
  comment even calls out dodging "the msg_in boot-swallow".)
- Fix: prompt is now STATE-derived, not a message. New `ShowSpawnPrompt()` in the game tick:
  a fragment exists + `PatchGameIniFromFragment(..., dryRun=true) == 1` (not already applied) +
  at least one player is online -> ChatNotify once per server session. The player-online check
  matters: without it the one-shot would fire into an empty server and be lost again.
  `PatchGameIniFromFragment` gained a `dryRun` param for the read-only probe. Removed the old
  QueueMsg prompt and the now-unused `spawnPrompted_` member from APClient.hpp.

## Added 2026-07-23 (tools): reset_ark_test.bat never wiped PER-PLAYER mailboxes

- Report: "used reset_ark and still no /confirm prompt - is something not getting wiped?" YES.
  The reset deleted `%PLUGIN%\ipc\<file>` only, but with /connect each survivor gets its own
  `ipc\<CharacterName>\` folder (the connect line literally says "mailbox Ghios"). Those
  subfolders survived every reset, so a stale `game_ini_fragment.txt` from an OLD seed persisted -
  which is why `/confirm` still had something to apply even though the current seed has
  randomize_dino_spawns off. Fix: `for /d %%D in ("%PLUGIN%\ipc\*") do rd /s /q "%%D"`.
  Verified incl. mailbox names containing spaces ("Bob Smith"); root-level ipc files preserved.
- NOT a bug (explains the missing prompt): the prompt (APClient.hpp ~631) only fires when slot_data
  actually carries `spawn_overrides` - i.e. `randomize_dino_spawns` is ON in that yaml - and only
  once per session (`spawnPrompted_`). The test yaml doesn't set the option at all -> default off
  -> no prompt, correctly.
- STILL OPEN (lower priority): `DoApConfirm` scans ANY mailbox for a fragment, and
  `WriteSpawnFragment` only removes it for the connecting slot. Stamping the fragment with the AP
  seed name (and ignoring foreign-seed fragments) would make this robust without needing a reset.

## Added 2026-07-23 (plugin): /confirm closed the server but never restarted it

- Report: `/confirm` patched Game.ini and shut the server down, but it never came back.
- Root cause: `SpawnRelauncher()` returned void and its `CreateProcessA` result was IGNORED -
  `DoApConfirm` called `TerminateProcess` regardless. So any spawn failure = server dies with no
  relauncher and no diagnostic. Most likely trigger: `CREATE_BREAKAWAY_FROM_JOB` fails with
  ERROR_ACCESS_DENIED when the server runs inside a job object that forbids breakaway (service
  wrappers / hosting panels).
- Fix (PluginMain.cpp): `SpawnRelauncher()` now returns bool, logs `GetLastError` on failure, and
  RETRIES without `CREATE_BREAKAWAY_FROM_JOB`. `DoApConfirm` starts the helper BEFORE saving/killing
  and, if it can't start, leaves the server RUNNING and tells the player to restart manually.
  The generated ap_restart.bat now also writes `ap_restart.log` (helper up / server exited /
  start issued + errorlevel) so a future failure is diagnosable. tasklist match uses /nh.
- Built v145 (VS18 Community). Verified no `_Mtx_*` imports (only pre-existing `_Thrd_*` from the
  websocket pump).
- ROUND 2 (still didn't restart): ap_restart.log stopped at "relauncher up, waiting for pid N" and
  a black console window titled `find /i "ShooterGameServer"` sat there forever. Cause: the helper
  runs DETACHED with no console and no std handles, and `tasklist | find` makes `find` read stdin -
  with an invalid stdin it BLOCKS, so the wait loop never advanced and the relaunch never ran.
  Two fixes: (1) replaced the poll with `powershell Wait-Process -Id <pid> -Timeout 300` - no
  stdin, no polling, returns immediately if the pid is already gone; (2) CreateProcess now passes
  inheritable NUL handles for stdin/stdout/stderr (STARTF_USESTDHANDLES + bInheritHandles=TRUE) so
  console tools in the detached helper always have valid handles. Verified live: the pattern blocks
  while the target lives and proceeds ~2s after it's killed, with no window and no hang.
- ROUND 3 (wait worked, relaunch failed): the helper reached the relaunch and cmd reported
  `'C:\...\start_ase_server.bat"  "TheIsland' is not recognized` - START glued the whole thing into
  one token. `start "" "x.bat" "arg"` (empty title) mis-parses. Rather than guess at another START
  quoting variant, dropped START entirely: the helper is now spawned with CREATE_NEW_CONSOLE
  (instead of DETACHED_PROCESS + NUL handles) so it owns a real console, and the bat uses
  `cd /d "<dir>"` + `call "<script>" "<map>"`. CALL has no title/parsing rules, and the server
  inherits the helper's console so `-log` output stays visible exactly like double-clicking the
  launcher. (The NUL-handle workaround is no longer needed and was removed.)
- FOLLOW-UP (user): reuse the host's own launcher instead of reconstructing the command line.
  `FindRestartScript()` walks UP (max 6 levels) looking for `start_ase_server.bat` - no config
  option (an earlier pass added a `restart_script` key; removed at user request). Searches from TWO
  anchors: the cwd AND the exe's own folder (GetModuleFileName), because start_ase_server.bat
  launches "%EXE%" without cd'ing, so the server's cwd is whatever folder the bat was run from -
  can't assume it's ...\Binaries\Win64. Verified against the real layout
  (C:\ArkServer\ArkServerLIVE with the bat in the root): found via both anchors. When found,
  ap_restart.bat runs that script after our pid vanishes - it already holds ports/cluster/save-dir,
  so nothing has to be re-quoted; otherwise it replays the cmdline.
  The RUNNING map is passed as arg 1 (`CurrentMapName()` parses the leading token of ARK's option
  string, e.g. "TheIsland?listen?..."), because start_ase_server.bat would otherwise fall back to
  its hardcoded default MAP - a silent map switch on restart. Returns "" for a flag/unknown, in
  which case the script keeps its own MAP. start_ase_server.bat itself needed NO change (its
  trailing `pause` runs only after the server exits, so it doesn't block the relaunch).
- Also removed `embedded_ap` from ArkAP.config.default.json (the external connector is being
  sunset, so the kill-switch no longer needs advertising). The CODE still reads the key and
  defaults it to true, so anyone with it already set in their ArkAP.config.json is unaffected.

## Added 2026-07-23 (apworld/data): Metal Pick + Metal Hatchet are now key (progression) items

- User: metal tools are key items, available ~smithy/metal-age time. Before: no logic referenced
  them -> classified `useful`, dropped anywhere.
- Wired in: aliased `Metal Pick`/`Metal Hatchet` in tame_logic + recipe `Forge` (crafted from metal
  ingots), so `has(tool)` also requires the Forge -> lands them at the metal-age tier, not sphere 0.
  Made them the gate for their matching harvest in `_EXTRA_GATES`: hatchet -> Collect 1000 Hide /
  Rare Flower x50; pick -> Collect 250 Silica Pearls / Collect 250 Oil. (Kill 100 Creatures stays
  Forge; Woolly Rhino Horn stays weapon.) This makes the tools REQUIRED -> progression.
- BUG caught + fixed in same pass: `_tame_required_items()` (progression classifier) only collected
  tame/cave/boss/note rules, NOT the kill-gates / `_EXTRA_GATES`. So gate-only engrams (Metal Pick/
  Hatchet) stayed `useful`, and the fill didn't guarantee them reachable before the checks needing
  them -> "Location Accessibility requirements not fulfilled" on Hide/Silica/Oil/Rare Flower.
  Fixed by also collecting the kill-gate + extra-gate + species-kill exprs into
  `_tame_required_items()`. Verified: 5-seed gen, no accessibility failures, all beatable.

## Added 2026-07-23 (apworld): tough KILL checks gated (water + apex) - tester sphere-realism

- Tester: hard kills (Rex, Mosasaur, Titanosaur, Megalodon, Tusoteuthis, Basilosaurus, Carcha, ...)
  all sat at sphere 0/1 because a `Killed: X` has no access rule (unlike `Tamed: X`). Wanted them
  deeper so ARK contributes late progression, not just early.
- Fix (set_rules): gate `Killed: X` by spawn_classes.json habitat/danger -
  water+apex -> `Scuba Tank + Crossbow`; water+mid -> `Scuba Tank`; land/air apex ->
  `Crossbow | Longneck Rifle`. Land alpha kills -> apex weapon floor (water alphas already
  progression-excluded). Easy (docile/mid land) kills stay early. The two shorthand systems
  (dino_tag vs spawn name) don't join cleanly, so `_KILL_HD_ALIAS`/`_KILL_HD_FORCE` fix the few
  gaps (Mosasaur->Mosa, Therizinosaurus->Therizino; Titanosaur + Basilosaurus forced).
- Never strands progression - the gate engrams (Scuba/Crossbow/Rifle) are always in the pool.
  Verified 3-seed gen: gated kills land spheres 14/27/28 (not 1); all beatable. Scope chosen by
  user: water + apex kills (levels/collection milestones left as-is for now).
- FOLLOW-UP (user): also gate `Killed: Unicorn` and `Kill 50+ Species`. Unicorn is trivial to kill
  but RARE - forced to ("land","apex") so it gets the weapon floor (`Crossbow | Longneck Rifle`,
  stage 2-3). `Kill 50 Species` -> `Crossbow + Scuba Tank` (Scuba pulls in Fabricator -> stage 3);
  `Kill 100 Species` -> `Longneck Rifle + Scuba Tank` (endgame). `Kill <50 Species` and the
  killtotal ("Kill N Creatures") milestones stay ungated. Verified: rules bind (empty inventory
  unreachable, gate items unlock); 4-seed gen beatable, no sphere-1 progression on them.
- FOLLOW-UP 2 (tester + Ghios: "bump all one higher"): light bump for rate/volume grinds off
  sphere 0/1 via `_EXTRA_GATES` - `Forge` (established-base marker) on `Collect 1000 Hide`,
  `Collect 250 Silica Pearls`, `Collect 250 Oil`, `Rare Flower x50`, `Kill 100 Creatures`; weapon
  floor (`Crossbow | Longneck Rifle`) on `Collect 5 Woolly Rhino Horn` (tanky source) and
  `Killed: Quetzal` (added to _KILL_HD_FORCE as air/apex - flies high, needs ranged). NOTE: used
  `Forge` not `Metal Hatchet` because Metal Hatchet has no tame_logic alias (would compile to
  always-true). Verified: gates compile to real `has`; Quetzal moved sphere 1 -> 15; 3-seed
  beatable, no sphere<=1 progression on any of the seven.

## Added 2026-07-23 (data): removed Acrocanthosaurus (not an ASE creature)

- Tester flagged `Killed: Acrocanthosaurus` as "not in ASE" - correct, Acrocanthosaurus isn't an
  ARK base-game creature (harvested from a modded/cross-version server by mistake). Removed the
  entry from data/dinos.json + apworld copy (106 -> 105 dinos); its `Tame:`/`Killed:` checks are
  built from dinos.json at runtime, so they vanish automatically. Added `NOT_IN_ASE = {"Acro"}` to
  tools/gen_dinos.py so a future regen drops the tag entirely (no tame item, no kill check).
  Verified: 0 Acrocanthosaurus refs in a fresh gen; still beatable.

## Added 2026-07-23 (apworld/data): Bow + Tranq Arrow is now an early tame method

- User: Bow + Tranq Arrow is an early, crucial tranq tool - it should be a real tame method and not
  be treated as late-game filler. Before, no creature used the `Bow KO` macro, so `Engram: Bow`
  classified as `useful` and could land on any excluded late check.
- Fix (tools/gen_tame_logic.py): a bare `Crossbow KO` tame method is now `Bow KO | Crossbow KO`
  (applied to the 13 open-world mid dinos + the plain-Crossbow cave-dweller tames; `Crossbow KO +
  <gear>` and `Rifle KO` untouched). `Bow KO = Bow + Tranq Arrow` crafts in inventory - NO
  Smithy/Forge - so it's a genuinely earlier path than `Crossbow KO` (which needs Anvil Bench +
  Forge). Regenerated data/tame_logic.json (+ apworld copy).
- Effect: `Engram: Bow` (and its arrow chain) now classify as progression -> protected from the
  excluded late-game checks, and its prereq-free recipe keeps it early-reachable. Verified: Bow in
  the required-engram set; 3-seed gen beatable. NOTE: it's an OR alternative, so the fill isn't
  FORCED to place Bow early - if we want it guaranteed sphere-0, add it to extra_early_items.

## Added 2026-07-23 (apworld): hard note families + deep-water alpha kills excluded from progression

- Playtest spoilers showed key progression on notes/kills a player may never realistically reach:
  `Genesis Chronicles #1-5`, `HLN-A Discovery #1-3`, `??? Note #1-5` (obscure cross-map narrative
  notes; "??? Note" is the real in-game name), and deep-water alpha kills. `_regions_flat` now
  EXCLUDES:
  - note families by prefix: `Genesis Chronicles`, `HLN-A Discovery`, `??? Note` (joins `Hologram:`).
  - ALL alpha kills (superseded the water-only rule below). An alpha realistically needs a good
    TAME to kill, and tames are locked behind Tame: items - so progression on one can strand a
    foundational engram behind a fight the player can't take. Playtest hit exactly this:
    `Killed: Alpha Carno -> Engram: Mortar And Pestle` at sphere 2. Now the whole `alpha_kills`
    category is filler-only (land + water). Verified 5-seed gen: no alpha on any critical path.
    (Originally water-only per an earlier scope choice; widened at user request.)
  - tame-only late-game dossiers: `Dossier: Rhyniognatha`, `Dossier: Carcharodontosaurus` (the
    dossier is only obtained by taming that very-late creature, so it's as gated as the tame).
- Verified across 5 gens: none of these hold progression / appear in any playthrough; all beatable.

## Added 2026-07-23 (apworld): boss Hologram checks excluded from progression

- `Hologram: Broodmother / Megapithecus / Dragon / Overseer` are viewed at the boss terminal, which
  needs the tributes/artifacts a boss run already requires - putting key progression behind a
  hologram loops it through boss prep. `_regions_flat` now marks any `Hologram: *` location EXCLUDED
  (filler-only). Verified: all four now hold filler/other-world items; still generates + beatable.

## Added 2026-07-23 (apworld): tame/breed COUNT milestones excluded from progression

- **Softlock class (same as notes/levels):** taming + breeding are locked behind `Tame:` items, but
  the milestone COUNT ("Tame 50 Creatures", "Breed 20 Dinos") isn't modelled in access logic - AP
  treats every count milestone as sphere-0. A playtest spoiler showed `Tame 50 Creatures ->
  Engram: Anvil Bench`; Anvil Bench gates the Crossbow/Rifle tranq chain used to tame, so a station
  needed to tame sat behind a taming grind. Could just as easily land on `Tame 50 Species` /
  `Breed 20 Dinos`, which are impossible without prior progression = hard softlock.
- **Fix:** `_regions_flat` now marks these tags EXCLUDED (filler-only): `milestone_tametotal_*`,
  `milestone_tames_*`, `milestone_breedtotal_*`, `milestone_first_breed`. Kill-count milestones and
  the small "Tame N Species" early ones for KILLS stay eligible (killing works with early weapons).
  Verified 5-player gen: all Tame/Breed milestones now hold filler/consumables, no gating engrams;
  still generates + beatable. (Scope chosen by user: Tame + Breed only.)

## Added 2026-07-23 (apworld): "Reach Level N" > 70 excluded from progression

- User: don't strand progression behind high level-ups. `_regions_flat` now marks any
  `Reach Level N` with N > 70 as LocationProgressType.EXCLUDED (filler/useful only). Levels <= 70
  still hold progression (Tame: Direbear L33, Parasaur L53, Kairuku L60, Metal Pick L22, etc).
  Verified in a 5-player gen: every level > 70 holds filler/useful/other-game items, no ARK
  progression; still generates + beatable.

## Added 2026-07-23 (apworld): RESOLVED softlock — start-granted engrams stranded tame checks

- **Bug:** `free_starter_engrams` removes start engrams from the item pool, but the tame logic still
  emitted `has("Engram: Waterskin")` (a starter) as a requirement. Since that item is never placed,
  the leaf was permanently unsatisfiable -> `Tamed: Achatina` and `Tamed: Ovis` (Sweet Veggie Cake
  passive tames, cake recipe needs Waterskin) were unreachable. Surfaced as generation warning
  "Could not access required locations ... Missing: [Tamed: Achatina, Tamed: Ovis]".
- **Fix:** `TameLogic.compile` / `required_items` take a `free` set of auto-granted ap_item_names;
  any `has(x)` where x is in `free` collapses to ('true',). `__init__._free_items()` =
  `_free_starter_names() | _bundled_saddle_names()`, threaded through `_tame_ast` / `_compile_expr`.
  Verified: warning gone, Achatina/Ovis reachable, still beatable. (Affects only pool-removed
  engrams referenced as tame reqs; tek grants are boss-gated and intentionally NOT freed.)

## Added 2026-07-23 (apworld): "Collect N Explorer Notes" >= 50 excluded from progression

- User: don't strand progression behind big note grinds. `_regions_flat` now marks any
  `milestone_notes_N` with N >= 50 as LocationProgressType.EXCLUDED (filler/useful only) - covers
  Collect 50/75/100/125/150/175/200/225/250. `Collect 25` still may hold progression. Verified:
  50+ milestones now hold structure/useful engrams, never progression; still generates + beatable.

## Added 2026-07-23 (apworld): playtest fixes — Electrophorus + cave-dweller tames

- **Electrophorus excluded from tame LOGIC but kept as item+check** (user: unrideable, useless to
  gate). `NO_TAME_LOGIC = {"Electrophorus"}` in __init__.py: its `Tame:` item stays (classified
  USEFUL, not progression), `Tamed:`/`Killed:` checks stay, but the combat + lock_taming rules are
  skipped and `Tamed: Electrophorus` is marked EXCLUDED (filler-only, since the plugin still gates
  taming in-game). NOTE: earlier I'd removed the item from dinos.json - user stopped that; RESTORED
  (id 8732030, tame_loc 8753029). gen_dinos.py NO_TAME does NOT list "Eel", so it won't re-remove.
- **Cave-dweller tames now require a cave survival floor** (`cave_tames` in tame_logic.json):
  Dung Beetle / Araneo / Pulmonoscorpius / Onyc / Megalania -> Crossbow KO; Megalosaurus -> Rifle
  KO; Arthropleura / Titanoboa -> Crossbow KO + Gas Mask (swamp). This OVERRIDES their sheet/tier
  method (survival dominates). Fixes the playtest report where a foundational engram
  (Mortar And Pestle) landed behind `Tamed: Dung Beetle` - the fill now routes the crossbow chain
  AROUND cave tames (they'd depend on it). Verified: Tamed: Dung Beetle now holds a tame item, not
  a station engram; Tamed: Electrophorus holds a useful engram (excluded).
- **RESOLVED (AUTHORITATIVE): cave DOSSIERS+NOTES gated** (`note_caves`, 41 entries). First pass used
  proximity+Z heuristics on the ASA map (pageid 72797) and MIS-flagged some (e.g. Rockwell #4).
  Corrected: the ASE map (pageid 69542, Data:Maps/Exploration/The Island/ASE) has EXPLICIT marker
  groups `"dossier cave"` and `"explorer-note cave"` - that's the ground-truth cave flag (visible as
  the "(cave)" coord tag in-popup). Each cave marker is assigned to its nearest `artifact cave cc:<x>`
  marker, so note -> artifact -> reuses cave_reqs. The 6 Cunning-cave notes are the underwater ones
  (cave_reqs[Cunning] already = Rifle KO + Scuba Tank), so no special underwater handling needed.
  Rockwell #4 correctly NOT gated now; Rex/Titanoboa/Cnidaria dossiers -> Cunning (scuba). Guarded so
  notes beyond dossier_checks are skipped. HOW TO REGEN: fetch
  `/api.php?action=query&prop=revisions&pageids=69542`, read markers["dossier cave"] +
  ["explorer-note cave"], assign each to nearest markers["artifact cave cc:<x>"].

## Added 2026-07-23 (apworld): BOSS / CAVE / TRIBUTE access logic (extends the tame-logic pass)

- **What:** the boss goal now requires real ARK prep instead of the Crossbow-KO interim floor.
  Each boss's "Defeated" event requires obtaining its ARTIFACTS (doing their caves); Overseer
  requires the 3 island bosses defeated first. Artifact + tribute checks (already
  `inventory_checks` in our data) got access rules too, so nothing is stranded behind a cave you
  can't survive or a dino you can't kill.
- **Data (data/tame_logic.json, seeded by gen_tame_logic.py):** `cave_reqs` (10 artifacts ->
  cave requirement expr), `boss_artifacts` (boss -> its artifacts), `overseer_bosses`,
  `tribute_dino` (organ -> source dino). New gear engrams aliased (Gas Mask, Ghillie->Ghillie
  Shirt, Fur->Fur Shirt) + their crafting-station recipes so "has the engram" also needs the
  station (no softlock crafting the gear). Cave reqs are BEST-EFFORT from ARK Island knowledge
  (combat floor + environment gear: swamp=Gas Mask+Ghillie, water=Scuba, cold=Fur, underwater
  cave=Scuba) - USER TO REVIEW via the artifact doc.
- **Rules (apworld set_rules):** artifact check -> `_cave_ast`; tribute check -> kill capability
  of the source dino (its tame combat, safe over-gate); boss event -> AND of its artifacts' caves
  (Overseer -> has the 3 Defeated events). Every engram any rule can require is forced PROGRESSION
  (37 in the default seed).
- **Verified:** boss ASTs correct (Broodmother=Crossbow-KO+Scuba, Dragon=Rifle-KO+GasMask+Ghillie+
  Fur+Scuba, Overseer=3 bosses); force-false test on Broodmother -> "Game appears unbeatable"
  (proves boss rules gate the goal); required-set includes Crossbow/GasMask/Scuba; solo, two-slot
  ARK+ARK, and maxed bundle_structures+lock_taming all generate + are beatable. NOTE: the minimal
  spoiler Playthrough can look short (2-3 spheres) when the fill places boss gear on early
  locations - that's valid (still gated), not a logic gap.
- **Decisions taken (user-approved 2026-07-23):** tributes gate their own checks only, NOT the
  goal (goal = any difficulty = Gamma = artifacts); Overseer = 3 bosses; gear tokens = the armor
  engrams; deep caves imply a water mount (folded into Scuba+combat for now). OPEN: exact cave
  requirements need the user's review; a water mount isn't modeled as an explicit tame yet.

## Added 2026-07-22 (apworld): tame/craft ACCESS LOGIC — softlock prevention (REPLACES progression_tiers)

- **What:** each "Tamed: X" AP check now requires the ENGRAMS X's taming method needs, so the fill
  can never strand a needed item behind a dino you can't yet tame (e.g. Rex requires the full
  Crossbow-KO chain: Crossbow + Arrow Tranq + Arrow Stone + Anvil Bench + Forge + Mortar And Pestle
  + Narcotic). AP-logic-only (no plugin change); the plugin still gates taming via Tame:X.
- **Data:** seeded from the user's "Ark IDs.xlsx" into `data/tame_logic.json` (+ apworld copy) by
  `tools/gen_tame_logic.py` - item recipe graph + dino tame reqs + engram-name aliases (our
  ap_names are scrambled: Smithy->Anvil Bench, Tranq Arrow->Arrow Tranq, Longneck Rifle->Simple
  Rifle, Metal Gate Frame->Metal Gateway, etc.). `apworld/ark_ase/tame_logic.py` parses the
  requirement expressions ('+'=AND, '|'=OR, parens - OR preserved faithfully), expands macros
  (Crossbow KO -> Crossbow + Tranq Arrow -> their stations) recursively through the recipe graph,
  and maps engram nodes -> AP item names with bundle_structures remapping (a bundled engram like
  Metal Gateway -> its Bundle item so the rule stays satisfiable).
- **Coverage:** the sheet covers 55 roster dinos; the other ~38 fall back to a requirement derived
  from the apworld's DINO_TIER table (T1->Slingshot|(Bola+Club), T2->Crossbow KO, T3->Rifle KO),
  plus a Deep Dive (scuba) requirement for deep-water tames (Mosa/Tuso/Plesio/Angler/Dunkle/
  Megalodon/Lio). tier0_add/tier0_remove now nudge this difficulty.
- **Classification:** every engram any tame rule can require is forced PROGRESSION (else AP wouldn't
  guarantee it's reachable before the tame it gates).
- **RETIRED:** progression_tiers, early_dino_checks, station_placement (+ tiered pre_fill/regions)
  are now IGNORED - the tame rules provide the ordering. Options kept defined so old yamls parse;
  default yaml sets them false. Single open region now; boss-defeat EVENTS gated behind a mid-game
  Crossbow-KO floor (interim goal reachability - real boss logic = cave artifacts + tributes is a
  later pass).
- **Verified:** tame_logic.py unit tests (OR semantics, macro expansion, DINO_TIER fallback, deep
  water) all pass; 68 non-trivial tame rules attach to real locations per generation; solo, two-slot
  ARK+ARK, and a maxed bundle_structures+lock_taming+sanity-trim config all generate + pass AP's
  completability calc.
- **DEFERRED / follow-ups:** cave-artifact access + boss tribute logic (goal is Crossbow-KO-gated
  interim); craft-dependent INVENTORY checks ("hold N Y") aren't ruled yet (overlap the cave work);
  confirm the 5 flagged engram-name aliases; dead tier code (TIER_GATES/_regions_tiered/_tier_of)
  left in place, remove in a cleanup pass.

## Added 2026-07-22 (plugin v87-boss-goal): boss kills are no longer AP check locations

- **Why:** a boss-kill CHECK could hold another player's item (spoiler showed "Boss: Overseer
  (Alpha): Dragon Egg Shard (hamza)") - and a near-impossible boss kill (e.g. Alpha Overseer) would
  strand it. Boss kills are now the GOAL only, not item-bearing locations.
- **Design:** removed the "bosses" category from `build_location_table` (Locations.py) + wherever
  `_used_locations` builds the set (__init__.py) - so boss check ids leave the datapackage entirely
  (critical: the plugin must never report a loc id that isn't a real AP location, or other clients
  hard-error - the DS3 class of bug). The boss "Defeated" EVENTS stay (completion_condition + region
  locked events derive names from the still-present bosses data), so AP's win logic is intact.
  New goal signal: on a boss kill the plugin appends the boss BASE-TAG (e.g. "SpiderBoss") to
  `boss_out.jsonl` in every known route's mailbox; slot_data now sends `goal_boss_tags` (ordered
  base-tags for the first N bosses) instead of `boss_groups` (loc ids); the embedded client + the
  external connector send CLIENT_GOAL once every required tag has appeared. Tek grants on boss kill
  unchanged. boss_out.jsonl resets with the seed (client ResetOnNewSeed + connector reset +
  reset_ark_test.bat). Location count dropped 657 -> 645.
- **Verified:** solo seed generates with ZERO "Boss:" locations + 4 Defeated events; the embedded
  client, fed the 4 base-tags via boss_out.jsonl, sent CLIENT_GOAL and the AP server logged
  "Team #1 has completed all of their games!".

## Added 2026-07-20 (plugin v86-item-names)

- **`/connect` now names the item it sends out** ("Ghios sent Engram: Campfire to Puff" instead of
  "sent an item to X"). The embedded client now fetches GetDataPackage for every room game on
  connect and maps slot->game (from Connected.slot_info) + game->{id->name}, used in both the
  ItemSend chat line and hints (which also gained the location name). Verified live against a
  two-slot local room.
- **BUG FOUND + FIXED while building this: `.items()` on a nlohmann `.value(...)` TEMPORARY dangles.**
  `for (auto& [k,v] : msg.value("slot_info", json::object()).items())` iterated NOTHING - the
  iteration proxy holds a reference to the temporary json, which is destroyed before the loop body
  runs (range-for lifetime-extension covers the proxy, not the underlying temporary). This silently
  emptied slot_info AND data.games -> names never resolved. Fix: bind `.value(...)` to a NAMED local
  first, then call `.items()`. (Range-for directly over a `.value(...)` ARRAY is safe - the
  top-level temporary IS extended; only the `.items()`-on-temporary form dangles.) RULE for this
  codebase: never `.items()` on a `.value()`/subscript temporary.

## Added 2026-07-20 (plugin v82-reconnect-throttle) — from live playtest feedback

- **Idle-disconnect never auto-reconnected (root cause of "had to reuse /connect").** The embedded
  client's websocket receive timeout was INFINITE (v79), so when the AP server idle-timed the
  connection out after ~2h with no close frame, `WinHttpWebSocketReceive` blocked forever - the
  session never noticed, never reconnected. Fixed: 40s receive timeout + on each timeout send a
  no-target Bounce (AP no-op) as a liveness probe; a dead socket surfaces as a send error ->
  reconnect. Live idle rooms just keep waiting (no spurious reconnects). Verified: killing the room
  mid-session flips conn_status connected -> "lost - reconnecting" within seconds.
- **Connect/disconnect now shown in chat (#1/#2).** New `conn_status.txt` per mailbox (single
  OVERWRITTEN "<seq>\t<msg>" line); the plugin tick announces it whenever seq changes - incl. once
  after a server restart (msg_in's boot backlog is swallowed, so connect status used to be eaten).
  Covers: connecting / connected / lost-reconnecting / refused.
- **Big simultaneous sends flooded the game (#5).** ~400 traps arriving at once were all applied in
  ONE game frame (hundreds of dino spawns) - hitched the server (playtest screenshot). Added a
  per-tick filler budget (FX_PER_TICK=6): expensive filler effects (spawn/give/buff) AND their chat
  lines throttle across ticks via g_pendingFx; cheap unlocks (engrams/tames) stay unthrottled.
- **`/connect` accepts EITHER arg order** (#4). New `<host:port> <slot>` (AP convention) is
  primary, but v84 auto-detects: the token that parses as host:port (numeric port) is the address,
  the other is the slot - so the old v76-v81 `<slot> <host:port>` still works and nobody's habit/
  instructions break. Password is tok[3+] in both. Slot-with-spaces still unsupported.
- **`/confirm` applies randomize_dino_spawns + self-restarts the server (#3).** New chat command:
  splices the client-written fragment into Game.ini as a managed block (case-insensitive section
  find; replaces any prior block; idempotent - a re-`/confirm` after apply is a no-op = no restart
  loop; 18-case standalone splice test all green), SaveWorld(true) blocking, spawns a DETACHED
  cmd that waits for our PID to exit then relaunches `GetCommandLineW()` verbatim, then
  `TerminateProcess(self)` - a HARD exit so ARK's graceful shutdown never rewrites Game.ini and
  wipes the patch. No relaunch script needed (the launch command is reused). Game.ini path
  auto-derived from `current_path()/../../Saved/Config/WindowsServer/Game.ini`, overridable via
  `"game_ini_path"` in ArkAP.config.json. Client prompts once per session. NOT yet tested on a
  live server (the process-restart path can't be exercised off-server) - watch the first live run.

## Fixed 2026-07-17: Styracosaurus removed (does not exist in ASE, GitHub issue #1)

- Confirmed via [seeno-beeno's report](https://github.com/Jbaker16163/Ark-Survival-Archipelago/issues/1):
  Styracosaurus has no wild spawn in ASE (Pre-Aquatica) - it's an ASA-only creature. Matches this
  project's own long-standing "still suspected phantom entry, unverified" note (see the disputed
  list below - now resolved by removing it instead of carrying the dispute forward).
  Removed its "Tame: Styracosaurus" item (id 8732080) + "Tamed:"/"Killed:" checks (8753079/8755079)
  from `data/dinos.json` + `apworld/ark_ase/data/dinos.json`; the tier-1 DINO_TIER entry in
  `__init__.py`; the harvest-tag friendly-name map + NO_SADDLE set in `tools/gen_dinos.py` (so a
  future queue regen can't reintroduce it under that name); regenerated the PopTracker pack
  (items.json/tracker.json/ap_map.lua all referenced "styracosaurus" codes) and the apworld.
  Item/location counts dropped by exactly 1/2 as expected; datapackage audit clean after rebuild.
  Ids retired, not reused (existing seeds keep working; a shipped seed with this item just becomes
  dead data that no longer resolves to anything in-game - acceptable for pre-release alpha).

## Pre-release audit 2026-07-17 (DS3-class datapackage bugs) — CLEAN, one gen-bug fixed

- Static audit (scratchpad audit_apworld.py, rerunnable): all 748 item + 659 location ids/names
  unique, `_used_locations()` categories == `build_location_table()` categories (the original
  DS3-bug shape), tek-grant/starter-engram/saddle-class cross-references all resolve. No
  datapackage inconsistencies -> nothing that can hard-error other players' clients.
- Real generation matrix (AP 0.6.7): maxed-tiered, tiers+trimmed-sanity, open-flat, two-slot
  multiworld, the user's real yaml, tiered+minimum-locations - all generate + pass AP's
  completability calc.
- **FIXED: `early_dino_checks: true` + `progression_tiers: false` cannot generate at ANY
  settings** (pool needs >= 372 item-eligible spots even fully shrunk; the mode only exposes 287 -
  AP bars useful items from EXCLUDED locations too). The old guard's advice ("raise
  dossier_checks to >= 540") was impossible (cap 240). Error message now says the truth: enable
  progression_tiers or disable early_dino_checks. Shipped yaml (both true) unaffected.

## ROOT CAUSE FOUND 2026-07-16 (v80-srwlock): std::mutex is FORBIDDEN in this plugin

- /connect crashed the ARK server 3× (v76-v79). Crash dump symbolication
  (ShooterGameServer.exe.552.dmp): `msvcp140!mtx_do_lock` null-deref on the FIRST
  `std::mutex::lock()` in APClient. The ARK server process loads an ANCIENT msvcp140.dll
  (14.16.27012 / VS2017-era); binaries built with modern MSVC STL (VS2022 17.10+ constexpr
  std::mutex layout) are incompatible with that dll's _Mtx_* functions. ANY std::mutex lock =
  instant AV. This is also the true, previously-undiagnosed cause of the old "Ipc std::mutex
  faulted in ReportCheck" incident (mutex was removed as a workaround without knowing why).
  FIX: `ApLock` (SRWLOCK wrapper, kernel32-only) in APClient.hpp; verified via dumpbin that the
  DLL no longer imports _Mtx_*. RULE: never std::mutex / std::condition_variable in this plugin -
  use ApLock. (v79's ws-first probe order + session-handle reuse were kept: correct hygiene,
  wrong suspect.)

## Added 2026-07-16 (plugin v76-embedded-ap) — EXPERIMENTAL, needs live server testing

- **Embedded AP client: in-game `/connect <slot> <host:port> [password]`, `/disconnect`,
  `/apstatus`.** No external connector process needed. Implementation: `APClient.hpp` — WinHTTP
  websocket (native `wss://` via Schannel, zero vendored TLS; wss-first with ws fallback like the
  Python connector), full AP protocol port (Connect/ReceivedItems-by-index/LocationChecks/
  Bounce-DeathLink/hints via Say !hint/goal via boss_groups/seed-reset). CRITICAL DESIGN: it
  drives the SAME per-route mailbox files as the external connector, so the plugin's apply/dedup/
  state logic is untouched and the external connector stays a drop-in alternative (rollback =
  don't type /connect; checkpoint commit 968aa5e predates the feature). Route = caller's survivor
  name -> in multiplayer the survivor does NOT need to match the slot name (unlike the external
  connector). Connections persist in `ap_connections.json` (room password in plain text - same
  trust level as connector.ini) and auto-resume on server start. Threads never touch ArkApi;
  clean join happens in the exported `Plugin_Unload` (loader-lock safe); status/errors surface in
  game chat via msg_in.jsonl.
  **Tested OFF-server against a real local AP 0.6.7 room** (standalone harness): handshake,
  ws fallback, check -> item round-trip (server logged "Ghios sent Engram: Tranq Dart... (Reach
  Level 2)", items_in.jsonl got {"item_id","from","index"}), DeathLink bounce sent, InvalidSlot
  refusal -> chat message + no retry spam, clean stop.
  **LIVE-VERIFIED 2026-07-16 (v80/v81)** after the std::mutex crash fix: two slots (Alice +
  Bob) connected simultaneously from game chat on the user's real server, items delivered.
  v81 added the route guard (refuse /connect while the survivor name is unresolved -> no more
  "_unnamed" mailbox binding) + same-slot session dedup on re-/connect.
  Known gaps: no DataPackage fetch (cross-game item names show as "an item"); spawn-randomizer
  writes game_ini_fragment.txt only (plugin-side boot patch scoped under Big features); slot
  names with spaces can't be typed into /connect; don't run embedded + external connector for
  the same slot simultaneously (double-send).

## Open bugs (reported, not yet fixed)

- **randomize_dino_spawns v3 (FULL OVERRIDE design) — BUILT 2026-07-15, needs one live test.**
  History: v1 NPCReplacements permutation proven broken live (chains resolve recursively; cycles
  cancel/void spawns; chain-free replacement forces extinctions = broken checks). v2 additions
  (foreign species ADDED alongside natives) tested working live but felt sparse - user wanted
  full randomization. v3, per user's insight, REPLACES each biome's roster outright via
  `ConfigOverrideNPCSpawnEntriesContainer` (cleaner than wiping via empty NPCReplacements: no
  density collapse from voided native rolls, no replacement semantics at all). All 96
  live-verified species are PARTITIONED round-robin across The Island's 14 major biome containers
  (grouped: land+air dealt across 7 land biomes ~10 each, water across 7 water zones ~3-4 each;
  chaos: everything across everything) - every species spawns somewhere by construction, so all
  Killed:/Tamed: checks stay obtainable (simulation-verified: all 96 assigned exactly once).
  Caves, bosses, alphas, tek, and specialty spawners (Giga/Quetz/beaver dams/Titanosaur/Unicorn)
  keep native rosters. slot_data key: `spawn_overrides`; connector renders
  ConfigOverrideNPCSpawnEntriesContainer lines (still renders legacy additions/replacements
  shapes for old seeds). LIVE-CONFIRMED WORKING by the user 2026-07-15 ("working well but very
  chaotic"). Follow-up shipped same day: `grouped` now DOWN-WEIGHTS predators (danger tags in
  spawn_classes.json: 10 apex @ EntryWeight 0.2, 31 mid @ 0.5, 55 docile @ 1.0 - slot_data
  entries are now [class, weight] pairs; connector accepts both shapes) so zones read as fauna
  with predators in them rather than predator walls. `chaos` deliberately keeps equal weights =
  the full predator-saturated experience. Needs one regen to take effect.

- **Reconnecting kills everyone** — root cause found + fixed in the connector (see Fixed below).
  Keep this line until the fix is confirmed live: needs one real reconnect test with the new
  `ArkConnector.exe` while another DeathLink player is connected.

## Big features (scoped, deliberately not started yet)

- **Dedicated ARK AP client (CommonClient GUI) + network bridge** — so each player runs a real AP
  client window on their OWN PC (like the Wind Waker client: colored item feed, Hints tab, command
  box) AND the bridging moves off the server PC. Interim answer that already works with zero code:
  players connect the generic **Archipelago Text Client** to the room AS their ARK slot (AP allows
  multiple connections per slot) - they get the full feed/hints/commands UI while the server-side
  connector keeps doing the mechanical work. The real feature: (a) build our client on Archipelago's
  CommonClient framework (GUI free, launches from the AP Launcher), (b) replace its file IPC with a
  small gateway process on the server PC that relays each player's ipc/<name> folder over TCP
  (~150 lines Python, plugin untouched; one gateway replaces N connectors). Avoid plugin-native TCP
  (invasive C++ in the crash-sensitive plugin).
- **YAML option to lock cave artifacts behind an item** — like `lock_taming` for artifacts. Related
  memory: `cave-locking-deferred.md` (idea C: physical entry block).

## Open design question (not a bug)

- **Alpha kills don't count toward the base species' kill check** — Alpha Carno fires its own
  `ALPHA-KILL` check but `KILL tag=Elite Carno` intentionally isn't in the species kill table, so it
  never also satisfies `Killed: Carno`. Decide whether it should (applies to all 7 alpha species).

## Fixed 2026-07-12 (creature roster corrections, user-confirmed)

- **Ghillie Shirt — closed, not a bug** (confirmed by user). Was a naming mismatch: in-game engram
  is "Ghillie Chestpiece".
- **Megacerops removed — confirmed NOT in ASE.** Deleted the `Tame: Megacerops` entry (was id
  8732048, tame_loc 8753047, kill_loc 8755047 — left as a permanent gap, not reused) from both
  `data/dinos.json` copies, dropped from the T2 `DINO_TIER` tuple in `__init__.py`, dropped from
  `NO_SADDLE` in `gen_dinos.py`. Total tameable+kill-only dinos now 107 (was 108). No dossier/note
  entry existed for it, so `locations.json` is untouched. Rebuilt apworld — verified gone from the
  shipped package.
- **Acrocanthosaurus confirmed real** (user override of the old "conflicting info" note) — already
  present and correctly wired (id 8732015, dino_tag "Acro", T2, no saddle in ASE), no code change
  needed. Removed from the disputed list.
- **Boss / Hologram checks never hold progression — already shipped, re-verified live in code.**
  This was the 2026-07-09 fix (`LocationProgressType.EXCLUDED` on all 12 `Boss: X` checks + 4
  `Hologram: X` notes, in both `_regions_tiered` and `_regions_flat`) — confirmed still intact
  after every subsequent change (Unicorn/Yeti re-add, multiplayer refactor, buff filler). No
  player can ever be required to defeat a boss or find a Hologram note to unlock something another
  player needs. Nothing to redo; flagged here since the user asked for it again.

## Added 2026-07-12 (plugin v72-buff-filler)

- **Buff/debuff filler items** — new filler effect kind `"buff"`: the plugin runs
  `ForceGiveBuff Buff_X true` as the TARGET player (multiplayer-routed; dead player -> retried
  after respawn like other filler). 18 positive buffs (XP boosts, Battle Tartare, insulation,
  Second Wind, ...) as goods + 37 debuffs (bleeds, stuns, bolas, Frozen Solid, Enrage Wildlife,
  Magic Mushrooms, ...) as traps, ids 8739600-8739617 / 8739700-8739736 in `data/filler.json`.
  Curated from the user's xlsx (`ark buffs & debuffs.xlsx`): EXCLUDED permanent/toggle effects
  (Leech, forced 3rd person, hover, ESP), near-lethal ones (GeyserLaunch fall death, 80%+ burns,
  Lamprey Poison perma-drain), 1-second cosmetics, broken ones (plain RageEffect), and the admin
  blink rifle the sheet itself says not to add. Filler mix is now 33 goods / 54 traps - dino-spawn
  traps are ~1/3 of trap rolls, debuffs ~2/3. **Buff classes are wiki-harvested: first time a few
  fire, check ArkAP_debug.log "BUFF applied cmd=" lines + confirm the effect landed in-game; a
  wrong class silently no-ops.** New seeds only (datapackage grows; regenerate).

## Added 2026-07-12 (plugin v71-multiplayer-slots) — EXPERIMENTAL, needs live testing

- **Multiplayer per-player slots** — several people on one ARK server, each their own AP slot.
  `ArkAP.config.json "multiplayer": true` (default false = solo, byte-for-byte old behavior — every
  route is the shared "" bucket + root ipc). Identity = survivor character name (sanitized).
  Per-player: engram gate/grants, taming gate, tame/kill/note checks, levels, inventory checks,
  milestones + counters (`counters.json` now `{"players":{...},"queue_pos":N}`; hooks append
  `<kind>\t<route>` to `events_queue.jsonl`, tick drains with persisted position), DeathLink
  (per-slot death_out/death_in), filler targets, /buyhint, starter engrams. Shared by design:
  crate access (HasItemAny), boss-kill checks (credit every known slot), tek grants ("" bucket =
  everyone). Each player = own connector instance with `ipc_dir = ...\ipc\<CharacterName>`.
  `state.json` new format `{"players":{name:{checked,received}}}` with legacy auto-migration into
  the shared bucket. Queue files now carry `\t<route>` (legacy lines parse as route ""). Plugin
  zip now ships `ArkAP.config.default.json`. **Solo regression risk is the thing to watch on the
  first v71 boot; multiplayer itself is untested until a second player joins.** Setup guide:
  docs/GETTING_STARTED.md "Multiplayer" section. Removed the redundant ClientNotifyTamedDino hook
  (first-tame milestone now derives from the tame counter).
- **Connector reconnect hardening** — auto-reconnect already existed (5s retry); now exponential
  backoff 5s→60s. With the earlier backlog fix both restart paths are safe: in-process reconnects
  keep the deaths_sent/hints_sent counters, process restarts skip the file backlog. The old
  "reconnecting kills everyone" line is now fully addressed pending one live confirmation.

## Added 2026-07-11 (feature pass — plugin v70-breeding-bundles-food)

- **randomize_dino_spawns (off/grouped/chaos)** — BUILT. Seed-deterministic wild-spawn shuffle via
  Game.ini `NPCReplacements` (idea + confirmation from Discord user a drunk Avocado). New
  `data/spawn_classes.json`: 103 curated Island spawn classes (71 land / 23 water / 9 air).
  Excluded on purpose: Giant Bee (hive mechanic), Titanosaur (map-limited mega spawn),
  Acrocanthosaurus/Megacerops/Styracosaurus (modded/unverified), bosses/alphas/events (never
  listed). grouped = shuffle within habitat (no drowning land dinos); chaos = one big shuffle.
  apworld `_npc_replacements()` -> slot_data pairs -> connector `_write_spawn_ini()` always writes
  `ipc/game_ini_fragment.txt`, and if connector.ini sets `game_ini=<path>` it patches Game.ini in
  place (marked BEGIN/END block only - other settings untouched; option off removes the block).
  Server restart applies. Kill/tame checks key on DinoNameTag so they survive the shuffle.
  **Class names are wiki-sourced, not harvested — first grouped run, spot-check a few swaps
  in-game; any typo'd class silently no-ops its one line.** No plugin changes (still v70).

- **food_sanity** — 14 food "hold N" inventory checks (ids 8757300-8757313, flagged `"food": true`
  in locations.json; sourced from ark.wiki.gg/wiki/Food per user's list, note: the crop is
  "Longrass" not "Longgrass"). Yaml option 0/25/50/75/100 (default 100) includes a random-per-seed
  percentage. Rides the existing plugin inventory-scan — zero plugin changes needed.
- **Breeding milestones** — "Breed your first dino" + "Breed 5/10/20 Dinos" (ids 8751039-8751042).
  Every mating event counts (egg or gestation), same species repeatable. Plugin hooks
  `APrimalDinoCharacter.DoMate`, counts the FEMALE side only (one event per pair), logs
  `BREED mate tag=X female=0/1` for both genders — **watch the log in the field**: if breeding never
  counts, DoMate may only fire on the male; flip the gender filter.
- **tame_sanity** — yaml option 25/50/75/100 (default 100): percentage of "Tamed: X" checks
  required (random-per-seed pick). All "Tame: X" unlock items stay in the pool. At 25% this drops
  72 locations — generation errors unless the pool shrinks too (bundle_structures etc); the error
  message names the knobs.
- **bundle_structures** — yaml toggle (default off): Wood(44)/Stone(38)/Metal(48)/Greenhouse(12)
  structure engrams (142 total, zero overlaps) replaced by 4 bundle items (ids 8738001-8738004,
  `STRUCTURE_BUNDLES` in Items.py, mirrored hardcoded in the plugin's ApplyStructureBundle).
  Rule: engram_class contains `PrimalItemStructure_` AND material is a word in the ap_name — tools
  (Metal Pick etc.) are not structures, stay individual. Bundle ids are ALWAYS in the datapackage;
  they're only pooled when the option is on.
- Balance at defaults: 588 items vs 661 total / 645 progression-eligible locations → 57 slack.

## Fixed 2026-07-11 (second pass — "handle the bug list")

All shipped in rebuilt artifacts: `dist/ark_ase.apworld`, `dist/ArkAP_plugin.zip` (plugin
**v69-dead-player-queue**), `dist/ArkConnector.zip` (exe rebuilt). Deploy = reinstall apworld +
plugin folder + connector, regenerate for the apworld/data changes to take effect.

- **Reconnect kills everyone (DeathLink backlog replay)** — `connector/ark_ap_connector.py` started
  `deaths_sent` at 0 every run while `ipc/death_out.jsonl` persists, so a connector restart
  re-broadcast every historical death as fresh DeathLink bounces (one room-wide kill per past
  death). Same latent replay existed for `hint_out.jsonl` (`!hint` spam). Both counters now start at
  the file's current end (`_line_count`), mirroring the plugin's own "backlog skipped on startup"
  rule. Prints `skipping DeathLink backlog: N old death(s)` on start.
- **Items received while dead were lost** — plugin's `DoGiveFiller` only required a non-null
  character, and a dead/dying pawn is still non-null → `GiveItem` landed in the corpse inventory
  (lost unless looted). `DoGiveFiller` and `DoSpawnTrap` now treat dead as "not in-world"
  (`IsDead()` check) → effect goes to the existing `g_pendingFx` retry queue and delivers after
  respawn. Engrams were never affected (granted to player STATE + reasserted every tick).
- **Leedsichthys no longer tameable** — `Tame: Leedsichthys` (8732042) removed; now a kill-only
  entry keeping its original kill check id 8755041. `gen_dinos.py` got a `KILL_ONLY_OVERRIDE` set
  that keeps it inside the main enumeration so no other dino's ids shift on regen.
- **ALPHA traps removed** — `(Trap) ALPHA Raptor/Carno/Rex` (8739530-32) dropped from
  `filler.json` (both copies). Remaining traps top out at Lone Spino / Hungry Rex.
- **??? notes now carry their wiki index** — renamed to `??? Note #N (idx 508/511/514/517/520)` so
  players can look them up on ark.wiki.gg Explorer_Notes/Locations. IDs unchanged (8740000+idx).
- **Re-added the previously-reverted set** (reverted 07-09 to bisect the datapackage bug; it was
  unrelated): Unicorn tame (8732104, Equus saddle), Yeti kill-only (8755115), Helena Note #30
  (idx 352), `_is_note()` notes-detection fix + sphere-2+ notes gate, boss/Hologram
  `LocationProgressType.EXCLUDED`. `dossier_checks` max/default now **240** (Options.py + ark.yaml).
- **gen_dinos.py ID-stability hardening** — `FORCED_KILL_LOC_BASE` restored to 8755110 (the earlier
  8755150 bump would have SHIFTED shipped kill ids for Onyc/Giant Bee/Rhynio/Carcha on regen); Yeti
  carries an explicit kill_loc (8755115) instead of a colliding formula slot; generator now asserts
  no duplicate id/tame_loc/kill_loc before writing.
- **build_release.py DLL path fixed** — pointed at `plugin/ArkAP/ArkAP/x64/Release/` (no dll there);
  actual MSBuild output is `plugin/ArkAP/x64/Release/ArkAP.dll`. Plugin zip now gets the real dll.

## Fixed 2026-07-11 (first pass)

- **Titanoboa / Dung Beetle kill tags never mapped** — `dino_tag` typos (`TitanBoa`→`Titanboa`,
  `Bettle`→`Beetle`) in both dinos.json copies. Kill checks silently never fired.

## Fixed 2026-07-09

- **ARK datapackage missing `alpha_kills`/`inventory_checks` locations** — `Locations.py`'s
  `build_location_table()` omitted 53 placed locations (e.g. 8757200), so any client hard-errored
  when one was referenced ("server violated the expected protocol"). Fixed to include all 6
  categories; the running room's frozen multidata was hand-patched (backup: `.archipelago.bak`).
  **Recurrence guard**: any category added to `_used_locations()` in `__init__.py` MUST also be in
  `build_location_table()` in `Locations.py`.

## Known-disputed / unverified (carried over)

- Unicorn/Yeti dino tags are best-guess — verify via `ArkAP_debug.log` `KILL tag=` / `TAME tag=`
  lines (mapped=1 means correct).
- Tier-gate reorder (Mortar And Pestle → Refining Forge → Smithy/Anvil → Fabricator as 4 sequential
  single-item gates) — requested, explained back, never confirmed to proceed.
- 7 engrams removed as "non-Island" are actually valid in ASE, but user explicitly declined to
  restore them. Intentional — don't "fix" by accident.

## Balance (after 2026-07-11 second pass)

Using the user's yaml (bundle_saddles etc): ~588 pool items vs 643 total locations, 627
progression-eligible (16 excluded: 12 bosses + 4 Holograms) → ~39 slack. Fits.
