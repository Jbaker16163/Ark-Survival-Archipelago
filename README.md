# ARK: Survival Evolved — Archipelago integration

Play **ARK: Survival Evolved** (The Island, Pre-Aquatica) as part of an [Archipelago](https://archipelago.gg)
multiworld. Engrams, dino taming, and supply-crate access are locked behind Archipelago items;
completing things in-game (reading explorer notes, taming/killing/breeding creatures, reaching
levels, holding food/resources, defeating bosses) sends checks to the multiworld.

Works alongside any other AP games. Includes DeathLink, in-game hints, an optional tech-tree
progression mode, buff/debuff + dino-spawn traps, food/tame "sanity" options, an experimental
several-players-on-one-server mode, and a PopTracker pack.

> **Alpha.** Full step-by-step setup + a smoke test to confirm it's working:
> **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**. Known issues: [BUGS.md](BUGS.md).

## Components

| Part | What it is | Runs on |
|------|-----------|---------|
| **`ark_ase.apworld`** | the Archipelago world (items/locations/logic/options) | the AP generator/host |
| **ArkAP plugin** | an [ArkServerApi (AseApi)](https://github.com/ArkServerApi/AseApi) C++ plugin that gates actions, reports checks, and connects to the AP room itself — type `/connect <slot> <host:port>` in game chat | the ARK dedicated **server PC** |
| **Connector** | optional external bridge (fallback for `/connect`; needed for the `randomize_dino_spawns` Game.ini auto-patch) | the server PC |
| **PopTracker pack** | optional visual auto-tracker | any PC |

```
ARK dedicated server ── ArkAP plugin ──(websocket, via in-game /connect)── Archipelago room
                                                                               │
                                                        PopTracker (auto-track)┘
```

---

## Install (players)

The short version is below; **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)** is the full
walkthrough (getting ASE onto the Pre-Aquatica branch via SteamCMD, ArkServerApi, LAN join, and a
smoke test). Everything except the game client runs on the **Server PC**.

### 1. Server PC — the plugin
1. Install **[ARK Server API](https://github.com/ArkServerApi/AseApi)** on your ASE
   dedicated server (Pre-Aquatica branch — see the full guide; BattlEye must be OFF).
2. Download **`ArkAP_plugin.zip`** from the [release](https://github.com/Jbaker16163/Ark-Survival-Archipelago/releases), unzip it, run
   **`install_plugin.bat`**, and point it at your `...\ArkApi\Plugins` folder.
3. The plugin defaults to **`ap`** mode (follow the AP room). Only an `ArkAP.config.json` with
   `"mode": "offline"` changes that — the usual "it does nothing" cause. Restart the server.

### 2. Archipelago — generate the game
1. Put **`ark_ase.apworld`** in your Archipelago install's `custom_worlds/` folder.
2. Copy the example yaml (`ark.yaml` inside the apworld), edit your options, drop it in `Players/`.
3. Generate + host the room; note its host:port.

### 3. Connect — in game chat
Join your server, spawn in, and type:
```
/connect YourSlotName archipelago.gg:38281
```
`/apstatus` to check, `/disconnect` to drop; reconnects automatically across server restarts.
(Alternative: the external **`ArkConnector.zip`** bridge — see the guide; it's the fallback, and
currently required for the `randomize_dino_spawns` Game.ini auto-patch.)

### 4. PopTracker (optional)
1. Install [PopTracker](https://github.com/black-sliver/PopTracker/releases).
2. Unzip **`ark_survival_evolved_ap.zip`** into PopTracker's `packs/` folder.
3. Load the pack → click **AP** → enter the room host:port + slot → connect. It auto-tracks.

---

## Options (yaml)

See `ark.yaml` for the full commented list and defaults; the highlights:

- **`goal`** — which bosses to defeat (Broodmother → +Megapithecus → +Dragon → all).
- **`progression_tiers`** — tech-tree mode: checks split into 4 tiers gated by
  Anvil Bench + Mortar And Pestle → Forge → Fabricator. `station_placement`
  (`tiered` / `local_early` / `global_early`) controls where those gate engrams land.
- **`lock_taming`, `lock_supply_crates`** — gate taming / crates behind AP items.
- **`bundle_saddles`** — grant a dino's saddle with its tame unlock (leaves the pool).
- **`bundle_structures`** — one item unlocks all Wood / Stone / Metal / Greenhouse structures.
- **`free_starter_engrams`** — start with the basic engrams (removed from the pool).
- **`extra_early_items`** — force specific named items early (routed like `station_placement`).
- **`food_sanity` / `tame_sanity`** — include a % of food-in-inventory checks / require a % of
  per-species tame checks.
- **`dossier_checks`** — how many explorer-note checks to include (max 240).
- **`randomize_dino_spawns`** — `off` / `grouped` / `chaos` wild-spawn shuffle (via Game.ini).
- **`death_link`, `trap_percentage`, `early_dino_checks`** — the usual extras. Traps are a mix of
  wild-dino ambushes and debuff effects; good filler includes buffs, kibble, and resources.

Several people can share one ARK server as separate AP slots (`"multiplayer": true` in
`ArkAP.config.json`; each player runs their own in-game `/connect`) — see the guide's
Multiplayer section.

---

## Build from source (developers)

Data files (`data/*.json`) are the single source of truth; the apworld, plugin, and tracker all
read them. Generators live in `tools/`.

```sh
python tools/gen_engrams.py <dump.json> --all   # engrams from a harvested dump
python tools/gen_dinos.py <dino_queue.jsonl>    # dinos from the tame harvest (+ forced/untameable)
python tools/gen_locations.py                   # notes / bosses / milestones / levels / inv checks
python tools/build_apworld.py                   # -> dist/ark_ase.apworld
python tools/gen_poptracker.py                  # -> poptracker/ pack
python tools/build_release.py                   # -> all release artifacts in dist/
```

- **Plugin:** build `plugin/ArkAP/ArkAP.sln` (x64 / Release) in Visual Studio or via MSBuild →
  `plugin/ArkAP/x64/Release/ArkAP.dll`. Deploy the DLL + `data/*.json` +
  `ArkAP.config.default.json` to `...\ArkApi\Plugins\ArkAP\`. Build marker is in
  `ARKAP_BUILD` (`src/PluginMain.cpp`) and written to `ArkAP_loaded.txt` on load.
- **Connector exe:** `connector/build_exe.bat` (needs `pip install pyinstaller websockets`).

`tools/build_release.py` regenerates the apworld + tracker and bundles the plugin (DLL + data +
default config), connector (exe + ini), and tracker zips into `dist/` ready to attach to a GitHub
release. The apworld, plugin, and PopTracker all read the same `data/*.json`, so after any data
change rebuild all of them (build_release does this) and **regenerate the seed** — a running room
has its datapackage frozen at generation time.
