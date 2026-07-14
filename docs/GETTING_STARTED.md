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
   │      + ArkServerApi + ArkAP.dll (plugin)     │
   │            │  (ipc files)                     │
   │        Connector (ArkConnector.exe)          │──(websocket)──►  Archipelago room
   └────────────┼─────────────────────────────────┘                      ▲
                │ LAN                                                     │ internet
        Your ARK game client                              Friends' games + AP clients
```

- **ARK dedicated server** — the game server, on the Pre-Aquatica beta branch.
- **ArkServerApi** — third-party server modding framework the plugin loads into.
- **ArkAP plugin (`ArkAP.dll`)** — gates actions and reports checks. Lives inside the server.
- **Connector** — bridges the plugin's local files to the Archipelago room over websocket.
- **apworld (`ark_ase.apworld`)** — the world definition, used on the machine that *generates* the seed.
- **PopTracker pack** — optional auto-tracker, any PC.

You (the ARK player) run the first three on the Server PC. Friends only need their own game + its
Archipelago client.

---

## What to download

From the [Releases page](../../releases), grab:

| File | What it's for | Goes on |
|------|---------------|---------|
| `ArkAP_plugin.zip` | the server plugin + data files | Server PC |
| `ArkConnector.zip` | the bridge (includes `ArkConnector.exe`) | Server PC |
| `ark_ase.apworld` | the Archipelago world | whoever generates the seed |
| `ark.yaml` | example player options (also bundled inside the apworld) | whoever generates the seed |
| `ArkServerScripts.zip` | launch/reset `.bat` helpers for the ARK server itself | Server PC |
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

---

## Step 2 — Install the ArkAP plugin

1. Unzip **`ArkAP_plugin.zip`**. It contains an `ArkAP\` folder (the `.dll`, the data files
   `engrams.json` / `dinos.json` / `locations.json` / `crates.json` / `filler.json`, and
   `ArkAP.config.default.json`) plus `install_plugin.bat`.
2. Run **`install_plugin.bat`**. When prompted, paste your ArkApi **Plugins** folder path, e.g.:
   ```
   E:\ARK\Server\ShooterGame\Binaries\Win64\ArkApi\Plugins
   ```
   It copies everything into `...\Plugins\ArkAP\`.
3. **Check the mode.** The plugin runs in **`ap`** mode (follow the Archipelago room) by default —
   that's what you want. It only switches to `offline` (self-randomize, no AP server) if a file
   named `ArkAP.config.json` next to the dll says so. So:
   - Fresh install, no `ArkAP.config.json` present → already `ap`, nothing to do.
   - If you have an `ArkAP.config.json` from earlier testing, open it and make sure it reads
     `"mode": "ap"` (a leftover `"mode": "offline"` is the #1 reason the multiworld "does nothing").
     `ArkAP.config.default.json` is a correct template.
4. Don't start the server yet — do Step 4 (generate) first so the connector has a room to point at.

> Manual alternative: copy the `ArkAP\` folder into `...\ArkApi\Plugins\` yourself.
> Multiplayer (several people on one server) is its own toggle in `ArkAP.config.json` — see the
> [Multiplayer](#multiplayer-on-one-ark-server-experimental) section.

---

## Step 3 — Install the connector

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
3. You'll run it in Step 5 (after the room exists). `ArkConnector.exe` needs no Python. If you
   prefer running from source, `run_connector.bat` auto-installs the one dependency (`websockets`).

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
   - **`ArkAP_loaded.txt`** — should read the current build marker, e.g. `v72-buff-filler`. If it's
     an older marker, you deployed a stale dll.
   - **`ArkAP_debug.log`** — look for a `LOAD ...` line near the top. It shows `mode=ap` (not
     `offline`) and non-zero counts like `engram_classes=`, `note_locs=`, `tame_dinos=`. If it says
     `mode=offline`, fix `ArkAP.config.json` (Step 2.3) and restart.
3. **Start the connector.** Double-click `ArkConnector.exe` (or `run_connector.bat`). On success it
   prints:
   ```
   [connector] connected as 'Ghios' (N locations remaining); goal = defeat ...
   ```
   Leave this window open — it auto-reconnects if the room drops.
4. **Friends connect** their own game's AP client to the same `host:port` with their slot names.
5. **Join your server in ARK.** Launch the client with **`-NoBattlEye`**, then join by **LAN IP +
   query port**, e.g. `192.168.x.x:27015` — *not* the game port 7777, and not your public IP
   (router hairpin issues). In-game console you can also use `open 192.168.x.x:7777`.

Now play. Learning an engram / taming / opening a crate is gated until AP grants it; collecting
notes, taming/killing, leveling, and boss kills fire checks out to the multiworld. Items friends
send you appear in-game automatically.

### Quick smoke test (is it actually working?)

Fastest end-to-end confirmation, once you're in-world:

- **Item IN:** in the **host's server console** (the ArchipelagoServer window, or the web room's
  command box) run `/send Ghios Engram: Spear` (use your own slot name). Within a second or two you
  should see an "Unlocked ..." message in ARK chat, and the connector window logs `received ...`.
  The Spear engram becomes learnable.
- **Check OUT:** collect an explorer note, or tame/kill a creature. The connector window logs
  `sending checks: ...` and any item placed there releases to its owner. `ArkAP_debug.log` shows a
  matching `REPORT loc=... -> checks_out.jsonl` line.

If both directions round-trip, the whole stack is working.

---

## Multiplayer on one ARK server (experimental)

Several people can play on the SAME dedicated server, each as their **own Archipelago slot** with
their own engram locks, tame unlocks, and checks. Identity = the **survivor character name** typed
at character creation (it can't be changed once set).

1. In `...\ArkApi\Plugins\ArkAP\ArkAP.config.json` set `"multiplayer": true` (see
   `ArkAP.config.default.json`) and restart the ARK server.
2. Each player adds their own ARK yaml to the generation (unique `name:`).
3. On the Server PC, run **one connector per player**. Copy the ArkConnector folder per player (or
   use `ArkConnector.exe --config playerB.ini`), each ini pointing at that player's mailbox:
   ```ini
   slot    = TheirSlotName
   ipc_dir = E:\ARK\Server\...\ArkApi\Plugins\ArkAP\ipc\TheirCharacterName
   ```
   **The subfolder name must be the survivor's character name, exactly.** The plugin creates it
   the first time that player does anything.
4. Everyone joins the ARK server with their own survivor. Done - each player's tames/kills/notes/
   levels/inventory/breeding count toward their own slot, engram locks are per-player, DeathLink
   is per-player.

Shared by design: supply-crate access (unlocked once ANY player has the item), boss kills (credit
every slot - arena fights are team efforts), and Tek engrams (unlock for everyone on a boss kill).
If more than one ARK yaml uses `randomize_dino_spawns`, only enable `game_ini` auto-patch on ONE
connector (there's a single Game.ini; last writer would win).

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
| `randomize_dino_spawns` | `off` | Shuffle wild spawns (`grouped` = within land/water/air, `chaos` = everything). Connector writes Game.ini NPCReplacements; one server restart applies. |
| `death_link` | `true` | Die together with linked players. |
| `trap_percentage` | `25` | % of filler slots that are traps (surprise wild-dino spawns). |
| `early_dino_checks` | `true` | Keeps other games' "early" items off ARK's hard/late checks. |

---

## Paths you'll likely need to change

This guide uses `E:\ARK\Server` as the install root. If yours differs, update **all** of these:

- **`start_ase_server.bat`** (from `ArkServerScripts.zip`) → `SERVER_ROOT` (and optionally `MAP`,
  ports, `ADMINPASS`).
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
