# Known bugs / open items

## Open bugs (reported, not yet fixed)

- **Reconnecting kills everyone** — root cause found + fixed in the connector (see Fixed below).
  Keep this line until the fix is confirmed live: needs one real reconnect test with the new
  `ArkConnector.exe` while another DeathLink player is connected.

## Big features (scoped, deliberately not started yet)

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

- Styracosaurus — still suspected phantom entry, unverified.
- Unicorn/Yeti dino tags are best-guess — verify via `ArkAP_debug.log` `KILL tag=` / `TAME tag=`
  lines (mapped=1 means correct).
- Tier-gate reorder (Mortar And Pestle → Refining Forge → Smithy/Anvil → Fabricator as 4 sequential
  single-item gates) — requested, explained back, never confirmed to proceed.
- 7 engrams removed as "non-Island" are actually valid in ASE, but user explicitly declined to
  restore them. Intentional — don't "fix" by accident.

## Balance (after 2026-07-11 second pass)

Using the user's yaml (bundle_saddles etc): ~588 pool items vs 643 total locations, 627
progression-eligible (16 excluded: 12 bosses + 4 Holograms) → ~39 slack. Fits.
