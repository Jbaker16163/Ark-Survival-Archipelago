# ARK: Survival Evolved × Archipelago — Getting Started (Alpha)

Play **ARK: Survival Evolved** (The Island, **Pre-Aquatica** branch) as one world in an
[Archipelago](https://archipelago.gg) multiworld. Your engrams, taming, and supply-crate access are
locked behind Archipelago items; reading explorer notes, taming/killing creatures, hitting level
milestones, and defeating bosses send checks out to everyone else's games.

> ⚠️ **This is an alpha.** Expect rough edges and known bugs (see [`BUGS.md`](../BUGS.md)). It runs
> on **ARK: Survival Evolved (ASE)**, *not* Survival Ascended, and only **The Island** has real data
> today. Everything below assumes a Windows Server PC.

---

## How it fits together

```
   Server PC
   ┌─────────────────────────────────────────────┐
   │  ARK dedicated server (Pre-Aquatica)         │
   │      + ArkServerApi + ArkAP.dll (plugin)     │──(websocket)──►  Archipelago room
   └────────────┼─────────────────────────────────┘                      ▲
                │ LAN                                                     │ internet
        Your ARK game client                              Friends' games + AP clients
```

- **ARK dedicated server** — the game server, on the Pre-Aquatica beta branch.
- **ArkServerApi** — third-party server modding framework the plugin loads into.
- **ArkAP plugin (`ArkAP.dll`)** — gates actions, reports checks, and connects to the
  Archipelago room itself: type **`/connect <slot> <host:port>`** in game chat and play.
- **apworld (`ark_ase.apworld`)** — the world definition, used on the machine that *generates* the seed.
- **Connector (`ArkConnector.zip`)** — optional external bridge; fallback for `/connect`, and
  currently needed for one thing: auto-patching `Game.ini` when `randomize_dino_spawns` is on.
- **PopTracker pack** — optional auto-tracker, any PC.

You (the ARK player) run the server + plugin on the Server PC. Friends only need their own game +
its Archipelago client.

---

## What to download

From the [Releases page](../../releases), grab:

| File | What it's for | Goes on |
|------|---------------|---------|
| `ArkAP_plugin.zip` | the server plugin + data files (includes the in-game `/connect` AP client) | Server PC |
| `ark_ase.apworld` | the Archipelago world | whoever generates the seed |
| `ark.yaml` | example player options (also bundled inside the apworld) | whoever generates the seed |
| `ArkServerScripts.zip` | launch/reset `.bat` helpers for the ARK server itself | Server PC |
| `ArkConnector.zip` | optional external bridge (fallback for `/connect`; needed for `randomize_dino_spawns` auto-patch) | Server PC |
| `ark_survival_evolved_ap.zip` | PopTracker pack (optional) | any PC |

You'll also need (external):

- **ARK: Survival Evolved dedicated server** — via SteamCMD (below).
- **ARK Server API** — https://github.com/ArkServerApi/AseApi (download from its Releases page)
- **Archipelago** — https://github.com/ArchipelagoMW/Archipelago/releases
- **PopTracker** (optional) — https://github.com/black-sliver/PopTracker/releases

---

## Step 1 — Install the ARK dedicated server (Pre-Aquatica)

The whole stack is pinned to the **Pre-Aquatica** branch (v358.24) — the last mod-compatible ASE
build. **Server and client must both be on this branch**, or you won't be able to join.

### 1a. Get the dedicated server with SteamCMD

1. Download SteamCMD: https://developer.valvesoftware.com/wiki/SteamCMD — unzip it to its own
   folder (e.g. `C:\ArkServer\steamCMD`).
2. Install the ASE dedicated server (app id **376030**) into a folder of your choice — this guide
   uses `E:\ARK\Server`. Run this **from a regular Command Prompt**, inside the SteamCMD folder
   (don't double-click `steamcmd.exe` first — see the troubleshooting note below if you do):
   ```
   steamcmd.exe +force_install_dir "E:\ARK\Server" +login anonymous +app_update 376030 -beta preaquatica validate +quit
   ```
   The `-beta preaquatica` flag is what pins it to the right branch. Wait for it to finish fully.

   > **"Command not found: steamcmd..."** — if you double-clicked `steamcmd.exe` instead of running
   > it from Command Prompt, it opens its own interactive `Steam>` console. You're now *inside*
   > SteamCMD, so retyping `steamcmd` or using `+` prefixes doesn't work there. Instead type each
   > command on its own line, no `+`, no leading `steamcmd`:
   > ```
   > force_install_dir E:\ARK\Server
   > login anonymous
   > app_update 376030 -beta preaquatica validate
   > quit
   > ```
   >
   > **"ERROR! Failed to install app '376030' (Missing configuration)"** — a known SteamCMD hiccup
   > with stale/corrupt local metadata, not a problem with your command. Fix, in order:
   > 1. Just run `app_update 376030 -beta preaquatica validate` again — often works on the second try.
   > 2. Still failing? Quit, delete `<steamcmd folder>\Steam\appcache` (and `depotcache` if present),
   >    relaunch, `login anonymous`, then `app_update` again.
   > 3. Still failing? Antivirus/Controlled Folder Access may be blocking the install folder — add
   >    an exclusion, and try running Command Prompt as Administrator.

   > **A fresh Windows Server PC is also likely missing the DirectX End-User Runtime** that
   > `ShooterGameServer.exe` needs (`X3DAudio1_7.dll` / `XAPOFX1_5.dll not found` errors when you
   > try to start the server later). Save yourself the trouble now: download and run the
   > [DirectX End-User Runtime (June 2010)](https://www.microsoft.com/en-us/download/details.aspx?id=8109)
   > web installer. See the Troubleshooting section below if you hit this after the fact instead.

### 1b. Put your **game client** on the same branch

In Steam: right-click **ARK: Survival Evolved** → **Properties** → **Betas** → in the beta dropdown
pick **`preaquatica`** (no access code needed). Let Steam finish updating before you launch.

### 1c. Install ARK Server API

Download from the [AseApi releases page](https://github.com/ArkServerApi/AseApi/releases) and
extract its contents into your install location's `\ShooterGame\Binaries\Win64` — it drops
`version.dll` (the loader) and an `ArkApi\` folder there. **BattlEye must be OFF** — ArkApi is
incompatible with it (the launch script below already passes `-NoBattlEye`).

> After this step you should have:
> `E:\ARK\Server\ShooterGame\Binaries\Win64\ArkApi\Plugins\`

### 1d. (Optional) Apply recommended server settings

Faster breeding/imprinting and quality-of-life rates make an Archipelago run a lot smoother.
`ArkServerScripts.zip` includes `apply_server_config.bat` + editable templates in `serverconfig\`
(`Game.ini.settings`, `GameUserSettings.ini.settings`).

1. **Start the server once, then stop it** — the config folder
   (`...\ShooterGame\Saved\Config\WindowsServer`) only exists after the first boot.
2. Edit the values in `serverconfig\*.settings` to taste (defaults: 10x baby maturation, faster
   hatching/mating, easier imprint; 5x taming, 2x harvest, 2x XP).
3. Run `apply_server_config.bat` (edit `SERVER_ROOT` at the top first). It does a **safe key-level
   merge** — only the keys in the templates are updated/added, everything else in your config is
   left alone, and a `.bak` backup is made. Restart the server.

---

## Step 2 — Install the ArkAP plugin

1. Unzip **`ArkAP_plugin.zip`**. It contains an `ArkAP\` folder (the `.dll`, the data files
   `engrams.json` / `dinos.json` / `locations.json` / `crates.json` / `filler.json`, and
   `ArkAP.config.json`) plus `install_plugin.bat`.
2. Run **`install_plugin.bat`**. When prompted, paste your ArkApi **Plugins** folder path, e.g.:
   ```
   E:\ARK\Server\ShooterGame\Binaries\Win64\ArkApi\Plugins
   ```
   It copies everything into `...\Plugins\ArkAP\`. On upgrades it **keeps your existing
   `ArkAP.config.json`** (never clobbers your settings).
3. **`ArkAP.config.json` is the plugin's settings file** (this exact name — the plugin reads no
   other). The shipped default is correct for a normal game: `"mode": "ap"` (follow the AP room;
   `offline` is a self-randomize test mode — a leftover `"mode": "offline"` is the #1 reason the
   multiworld "does nothing") and `"multiplayer": false` (flip to `true` for several players on
   one server — see the Multiplayer section).
4. Don't start the server yet — do Step 4 (generate) first so the connector has a room to point at.

> Manual alternative: copy the `ArkAP\` folder into `...\ArkApi\Plugins\` yourself.
> Multiplayer (several people on one server) is its own toggle in `ArkAP.config.json` — see the
> [Multiplayer](#multiplayer-on-one-ark-server-experimental) section.

---

## Step 3 — Connect to Archipelago: in-game `/connect`

The plugin has a built-in AP client — **no extra software to run**. Once the room exists
(Step 4), spawn in on your survivor, then type in game chat:

```
/connect <YourSlotName> <host:port> [password]     e.g. /connect Ghios archipelago.gg:38281
```

- `/apstatus` shows the connection(s); `/disconnect` drops yours.
- Connections **auto-resume** when the ARK server restarts, and reconnect with backoff if the
  room goes down. If chat says *AP refused*, fix the slot/password and `/connect` again.
- **Multiplayer:** each player spawns in fully, then runs their own `/connect` — the plugin
  identifies them by their survivor automatically (survivor name does NOT need to match the
  slot). If it says it can't read your survivor name yet, wait a few seconds and retry.
- Slot names with **spaces** can't be typed into `/connect` — use space-free `name:` values in
  your yamls.
- The address must be reachable **from the Server PC** (chat commands run on the server): a room
  on the server's LAN works for remote players too.

**One current limitation:** with `randomize_dino_spawns` enabled, the in-game client can't patch
`Game.ini` (ARK rewrites that file at shutdown). It writes `ipc\game_ini_fragment.txt` and tells
you in chat — paste that into `Game.ini` while the server is stopped, or use the external
connector below once with `game_ini=` set. Plugin-side auto-patch at boot is planned.

### Alternative — external connector (fallback)

Fully supported; use it if `/connect` misbehaves (its console output is handy for
troubleshooting) or for the `randomize_dino_spawns` auto-patch. **Don't run both the in-game
client and an external connector for the same slot at once** — they'd double-send.

1. Unzip **`ArkConnector.zip`** anywhere on the Server PC (it has `ArkConnector.exe`,
   `connector.ini`, `run_connector.bat`).
2. Open **`connector.ini`** and set:
   ```ini
   [connector]
   server   = archipelago.gg:38281          ; your room address (from Step 4)
   slot     = Ghios                         ; must match name: in your yaml, case-sensitive
   password =                               ; room password, blank if none
   ipc_dir  = E:\ARK\Server\ShooterGame\Binaries\Win64\ArkApi\Plugins\ArkAP\ipc
   death_link = true
   ```
   **`ipc_dir` must point at the `ipc` folder inside the plugin you just installed.** The plugin
   creates that folder on first server start.
3. Run it after the room exists. `ArkConnector.exe` needs no Python. If you prefer running from
   source, `run_connector.bat` auto-installs the one dependency (`websockets`).

---

## Step 4 — Generate & host the Archipelago seed

Do this on whatever machine is the "generator" (can be your Server PC, or a friend hosting).

1. **Install Archipelago** (link above). Note its folder — it has `custom_worlds\`, `Players\`,
   `ArchipelagoLauncher.exe`.
2. **Install the apworld:** drop `ark_ase.apworld` into Archipelago's `custom_worlds\` folder (or
   Launcher → *Install APWorld*).
3. **Add yamls:** put your ARK yaml in `Players\`. Start from the example `ark.yaml` bundled in the
   apworld — set `name:` to your slot (must match `connector.ini`). Each friend drops in their own
   game's yaml with a unique `name:`. See [Options](#options-your-yaml) below.
4. **Generate:** Launcher → *Generate* (or run `ArchipelagoGenerate.exe`). Output `AP_xxxx.zip`
   lands in `output\`. If it errors, the message names the offending yaml.
5. **Host** — pick one:
   - **archipelago.gg (easiest):** upload the zip at https://archipelago.gg/uploads → *Host Game*.
     It gives you a `host:port` like `archipelago.gg:38281`. Share it.
   - **Self-host:** Launcher → *Host* → pick the zip. Serves on `:38281`; friends need your public
     IP and a forwarded port.
6. Put that `host:port` (and password, if any) into `connector.ini`.

---

## Step 5 — Start everything & play

**Order matters:**

1. **Start the ARK dedicated server.** Download **`ArkServerScripts.zip`** from the release, unzip
   it anywhere, and use `start_ase_server.bat` (edit `SERVER_ROOT` at the top to your install path
   first). It launches The Island with `-NoBattlEye`.
2. **Confirm the plugin loaded** (do this once to know it's wired up). Two files appear in
   `...\ArkApi\Plugins\ArkAP\`:
   - **`ArkAP_loaded.txt`** — should read the current build marker, e.g. `v81-route-guard`. If it's
     an older marker, you deployed a stale dll.
   - **`ArkAP_debug.log`** — look for a `LOAD ...` line near the top. It shows `mode=ap` (not
     `offline`) and non-zero counts like `engram_classes=`, `note_locs=`, `tame_dinos=`. If it says
     `mode=offline`, fix `ArkAP.config.json` (Step 2.3) and restart.
3. **Friends connect** their own game's AP client to the same `host:port` with their slot names.
4. **Join your server in ARK.** Launch the client with **`-NoBattlEye`**, then join by **LAN IP +
   query port**, e.g. `192.168.x.x:27015` — *not* the game port 7777, and not your public IP
   (router hairpin issues). In-game console you can also use `open 192.168.x.x:7777`.
5. **Connect to the room from game chat** (once spawned in):
   ```
   /connect Ghios archipelago.gg:38281
   ```
   Chat replies `AP: connected as 'Ghios'`; `/apstatus` confirms. This persists — after a server
   restart it reconnects by itself. (Using the external connector instead? Start
   `ArkConnector.exe` now and leave its window open.)

Now play. Learning an engram / taming / opening a crate is gated until AP grants it; collecting
notes, taming/killing, leveling, and boss kills fire checks out to the multiworld. Items friends
send you appear in-game automatically.

### Quick smoke test (is it actually working?)

Fastest end-to-end confirmation, once you're in-world:

- **Item IN:** in the **host's server console** (the ArchipelagoServer window, or the web room's
  command box) run `/send Ghios Engram: Spear` (use your own slot name). Within a second or two you
  should see an "Unlocked ..." message in ARK chat. The Spear engram becomes learnable.
- **Check OUT:** collect an explorer note, or tame/kill a creature. The room console shows the
  check arriving, and `ArkAP_debug.log` shows a matching `REPORT loc=... -> checks_out.jsonl` line.

If both directions round-trip, the whole stack is working.

---

## Multiplayer on one ARK server (experimental)

Several people can play on the SAME dedicated server, each as their **own Archipelago slot** with
their own engram locks, tame unlocks, and checks. Identity = the **survivor character name** typed
at character creation (it can't be changed once set).

1. In `...\ArkApi\Plugins\ArkAP\ArkAP.config.json` set `"multiplayer": true` and restart the ARK
   server. Confirm in-game with `/whoami` — it should say `multiplayer=ON` and show your
   character's mailbox route.
2. Each player adds their own ARK yaml to the generation (unique `name:`).
3. Connect each player — easiest is in game chat: each player **spawns in fully on their
   survivor**, then types their own:
   ```
   /connect TheirSlotName archipelago.gg:38281
   ```
   (the plugin routes by their survivor automatically; `/apstatus` to verify; if it says it
   can't read your survivor name yet, wait a moment and retry). OR run
   **one external connector per player** on the Server PC. Copy the ArkConnector folder per player
   (or use `ArkConnector.exe --config playerB.ini`). Every ini uses the **same** `ipc_dir` (the
   plugin's root `ipc` folder); each sets `multiplayer = true` and its own `slot`:
   ```ini
   slot        = TheirSlotName
   ipc_dir     = E:\ARK\Server\...\ArkApi\Plugins\ArkAP\ipc
   multiplayer = true
   ```
   The connector auto-creates and uses `ipc\TheirSlotName` at startup — no per-player paths to type.
   **Each player's ARK survivor name must equal their slot, exactly as `/whoami` shows it** (that's
   the route the plugin writes to). Leave `multiplayer = false` for solo play.
4. Everyone joins the ARK server with their own survivor. Done - each player's tames/kills/notes/
   levels/inventory/breeding count toward their own slot, engram locks are per-player, DeathLink
   is per-player.

Shared by design: supply-crate access (unlocked once ANY player has the item), boss kills (credit
every slot - arena fights are team efforts), and Tek engrams (unlock for everyone on a boss kill).
If more than one ARK yaml uses `randomize_dino_spawns`, only enable `game_ini` auto-patch on ONE
connector (there's a single Game.ini; last writer would win).

### Port forwarding (friends joining over the internet)

Everyone still joins the **same one ARK server** (multiplayer here is per-player AP slots, not
separate ARK server instances), so the ports below don't multiply per player — forward them once
on the **Server PC's router**, matching whatever you set in `start_ase_server.bat`. Only the person
hosting needs to forward anything; joining players don't.

| Port | Protocol | What it's for | Needed when |
|------|----------|----------------|-------------|
| `7777` (`GAMEPORT`) | UDP | Main game connection | Always |
| `7778` (`GAMEPORT`+1) | UDP | ASE claims this automatically alongside the game port | Always |
| `27015` (`QUERYPORT`) | UDP | Server browser / query (what clients actually connect through) | Always |
| `27020` (`RCONPORT`) | TCP | Remote console access | Only if you use RCON tools remotely - skip for local-only admin |
| `7779`/`7780` (bridge `GAMEPORT`/+1) | UDP | Temporary bridge server for live map-to-map transfers | Only while running `start_transfer_server.bat` with a remote friend transferring |
| `27016` (bridge `QUERYPORT`) | UDP | Bridge server query | Same as above |
| `27021` (bridge `RCONPORT`) | TCP | Bridge server RCON | Only if using RCON on the bridge remotely |
| `38281` | TCP | The Archipelago room itself | Only if self-hosting the AP room (Launcher → Host) instead of using archipelago.gg |

The **connector** makes an outbound-only connection (to the AP room) — it never needs a forwarded
port itself, in solo or multiplayer mode. If you only ever transfer characters locally (no remote
friend needs to join the bridge mid-transfer), you can skip the bridge ports entirely.

## Step 6 — PopTracker (optional)

1. Install [PopTracker](https://github.com/black-sliver/PopTracker/releases).
2. Unzip `ark_survival_evolved_ap.zip` into PopTracker's `packs\` folder.
3. Load the pack → click **AP** → enter the same `host:port` + your slot → connect. It auto-tracks.

---

## Options (your yaml)

Edit these in your `ark.yaml` before generating (full commented list is in the file):

| Option | Default | What it does |
|--------|---------|--------------|
| `goal` | `all_bosses` | Which bosses to defeat to win (cumulative Broodmother → Megapithecus → Dragon → all). Any difficulty counts. |
| `lock_taming` | `true` | Taming a creature requires its `Tame: X` item first. |
| `lock_supply_crates` | `true` | Beacons/crates gated behind AP items. |
| `progression_tiers` | `true` | Tech-tree mode: checks split into tiers gated by Mortar & Pestle / Smithy → Forge → Fabricator. |
| `station_placement` | `global_early` | Where tier-gate engrams land (`tiered` / `local_early` / `global_early`). |
| `bundle_saddles` | `false` | Grant a dino's saddle together with its tame unlock. |
| `free_starter_engrams` | `false` | Start with basic engrams (removed from the pool). |
| `dossier_checks` | `240` | How many explorer-note checks to include (max = all Island notes). |
| `food_sanity` | `100` | % of the 14 food "hold N in inventory" checks included (0/25/50/75/100). |
| `tame_sanity` | `100` | % of per-species "Tamed: X" checks required (25/50/75/100). Lower = fewer required tames; all Tame unlock items stay in the pool. |
| `bundle_structures` | `false` | One item unlocks ALL structures of a material (Wood/Stone/Metal/Greenhouse). Tools stay individual. |
| `randomize_dino_spawns` | `off` | Fully randomize which species live in which biome — each zone's roster is replaced by a seeded hand, every species still spawns somewhere (`grouped` = habitat-appropriate, `chaos` = anything anywhere). Connector writes the Game.ini lines; one server start applies. |
| `death_link` | `true` | Die together with linked players. |
| `trap_percentage` | `25` | % of filler slots that are traps (surprise wild-dino spawns). |
| `early_dino_checks` | `true` | Keeps other games' "early" items off ARK's hard/late checks. |

> **`randomize_dino_spawns` needs the full chain to actually do anything:** (1) set it in the yaml
> you **generate from**, with the current `ark_ase.apworld` installed (older apworlds don't emit it);
> (2) set `game_ini = ...\Game.ini` in `connector.ini` so the connector auto-writes the
> spawn-addition lines (otherwise it only drops `ipc\game_ini_fragment.txt` for you to paste) —
> and make sure the ARK server is **stopped** when the connector patches Game.ini (ARK rewrites
> the file from memory on shutdown, wiping edits made while it runs); (3) start the ARK server;
> (4) run `cheat DestroyWildDinos` in-game to force a respawn wave (existing wild dinos predate
> the change). If the connector window never prints `spawn randomizer: N container additions`,
> it's step 1 — regenerate.

---

## Paths you'll likely need to change

This guide uses `E:\ARK\Server` as the install root and `E:\ARK\ServerCluster\...` for the
pseudo-cluster data. If yours differs, update **all** of these:

- **`start_ase_server.bat`** (from `ArkServerScripts.zip`) — edit the block at the top:
  - `SERVER_ROOT` — your real install path.
  - `MAP`, `SESSION`, `MAXPLAYERS`, `GAMEPORT`/`QUERYPORT`/`RCONPORT` — optional, defaults are fine
    for a single-map solo server.
  - `ADMINPASS` — **change this from the placeholder.** It's your server's RCON/admin password.
  - `SERVERPASS` — leave blank for no join password, or set one.
  - `CLUSTERID` — pick your own unique name (any string; just needs to match across scripts —
    see below). Leave blank to disable clustering entirely (single map, no pseudo-cluster).
  - `CLUSTERDIR`, `SAVESROOT` — where cluster tribute data and per-map saves live. Can be
    anywhere with disk space; doesn't have to be under `SERVER_ROOT`.
  - `TRIBUTEEXP` — how long Obelisk uploads last before expiring (seconds). Default 30 days.
- **`start_transfer_server.bat`** (only needed for live character transfers between maps) —
  `SERVER_ROOT`, `CLUSTERID`, `CLUSTERDIR`, and `SAVESROOT` **must exactly match**
  `start_ase_server.bat`'s values, or the bridge server won't write into the same place your main
  server reads from.
- **`switch_map.bat`** — `SERVER_ROOT` and `SAVESROOT` (same values again).
- **`reset_ark_test.bat`** — `SERVER_ROOT`, `CLUSTER`, and `MAPSAVES` (same values as
  `CLUSTERDIR`/`SAVESROOT` above) so a reset actually clears the real data, not just placeholders.
- **`connector.ini`** → `ipc_dir` (must be your real `...\ArkApi\Plugins\ArkAP\ipc`).
- **`install_plugin.bat`** prompt → your real `...\ArkApi\Plugins` path.

---

## Starting a fresh seed later

A generated game is a new seed — clear old progress or stale unlocks/checks leak in. On the Server
PC, with the ARK server **stopped**:

- **Run `reset_ark_test.bat`** (from `ArkServerScripts.zip`, edit `SERVER_ROOT` at the top first).
  It backs up + clears the
  world save and wipes every ArkAP tracking file: `state.json`, `seed.json`, `counters.json`,
  `applied_index.json`, `events_queue.jsonl`, the `ipc\` mailbox files, all the `*_queue.jsonl`
  harvest files, and every multiplayer `ipc\<player>` subfolder. This is the whole reset — do it
  before hosting each new seed.

The connector keeps no state; on reconnect AP resends everything and the plugin dedups by item
index, so you only ever reset the plugin + world, never the connector.

---

## Troubleshooting

- **`ShooterGameServer.exe - System Error`: "X3DAudio1_7.dll was not found" / "XAPOFX1_5.dll was
  not found" (usually both, one after the other) when starting the server** — missing the DirectX
  End-User Runtime; Windows' built-in DirectX doesn't include these legacy audio components, and
  ARK's server still links against them even though it's headless. Download and run the
  [DirectX End-User Runtime (June 2010)](https://www.microsoft.com/en-us/download/details.aspx?id=8109)
  web installer, then retry. Note: that installer just **extracts** a folder of `.cab` files first —
  extraction isn't installation. Find **`DXSETUP.exe`** in the extracted folder and run it (as
  Administrator) to actually install the DLLs; then start the server again.
- **Can't see / join the server** — confirm client is on `preaquatica`, launched with `-NoBattlEye`,
  and you're using **LAN IP + query port `:27015`** (not 7777, not public IP).
- **"Unable to query server info for invite" / Steam Join fails** — use ARK's own direct connect
  (`open <ip>:<port>` in console) instead of Steam's Join button.
- **Plugin didn't load** — check `ArkAP_debug.log` for a `LOAD` line; verify BattlEye is off and
  ArkApi's `version.dll` is present in `Win64\`.
- **"server violated the expected protocol ... location XXXXXXX"** — the seed was generated with an
  older/mismatched apworld. Regenerate with the current `ark_ase.apworld` installed (and make sure
  the plugin's `locations.json` came from that same build).
- **Multiworld "does nothing" — no locks, no unlocks** — almost always `ArkAP.config.json` set to
  `"mode": "offline"`. Set it to `ap` (or delete the file) and restart. Confirm via the
  `ArkAP_debug.log` `LOAD ... mode=ap` line.
- **Received an item but it didn't appear in-game** — check `ArkAP_debug.log` for an `APPLY id=...`
  line (plugin got it) and a `grant:` line (engram pushed). Items that arrive while your character
  is dead are now **held and delivered on respawn** (not lost) — if you were dead, respawn and it
  lands. Engram names can differ in-game (e.g. "Ghillie Shirt" unlocks as **Ghillie Chestpiece**) —
  search the engram list before assuming it failed.

Known issues and planned fixes live in [`BUGS.md`](../BUGS.md).
